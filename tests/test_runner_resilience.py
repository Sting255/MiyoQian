# -*- coding: utf-8 -*-
"""P5：单个账号的接口/网络异常不能吃掉当天剩下的账号。

现在 games.py / bbs.py / cloud_games.py 里没有兜底，
`ApiClient` 抛出的 ApiError（超时、5xx、429、非 JSON）会一路穿透
`GameCheckin.run` → `runner._run_tasks` 的账号循环，
于是：后续账号当天不再执行，且 runner 末尾的 save_config 永不执行
（刚在 login.py 里刷新出来的 cookie_token 就丢了）。
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from miyouqian.core.config import load_config
from miyouqian.core.http import ApiError
from miyouqian.service import runner
from miyouqian.service.notifier import is_task_success
from tests.support import IsolatedConfigTest, base_config


class _Ctx:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *exc: object) -> bool:
        return False


def _fake_client(*args: object, **kwargs: object) -> _Ctx:
    return _Ctx()


class ResilientRunnerTest(IsolatedConfigTest):
    def _run(self, behaviours: dict[str, object]):
        config = base_config()
        config["accounts"] = [{"name": name} for name in behaviours]
        self.write_config(config)
        loaded = load_config(self.config_path)

        attempted: list[str] = []
        saved: list[str] = []

        class FakeGameCheckin:
            def __init__(self, client, config, account, emit=None):
                self.account = account

            def run(self, only_games=None):
                name = str(self.account.get("name"))
                attempted.append(name)
                outcome = behaviours[name]
                if isinstance(outcome, Exception):
                    raise outcome
                return list(outcome)  # type: ignore[arg-type]

        with (
            # 账号间等待由 base_config() 里的 account_gap 关掉了，这里不再需要打补丁
            mock.patch.object(runner, "GameCheckin", FakeGameCheckin),
            mock.patch.object(runner, "ApiClient", _fake_client),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda path, cfg: saved.append(str(path))),
        ):
            lines = runner.run_tasks(loaded, str(self.config_path))
        return lines, attempted, saved

    def test_failing_account_does_not_abort_the_rest(self) -> None:
        lines, attempted, saved = self._run(
            {
                "A": ApiError("网络请求失败: 超时"),
                "B": ["原神 已签到 「精锻用良矿」x3", "游戏社区签到汇总：成功 1，失败 0，跳过 0"],
            }
        )
        self.assertEqual(attempted, ["A", "B"], "第一个账号报错后，第二个账号没有被执行")

    def test_failure_is_reported_and_marked_unsuccessful(self) -> None:
        lines, _, _ = self._run(
            {
                "A": ApiError("网络请求失败: 超时"),
                "B": ["游戏社区签到汇总：成功 1，失败 0，跳过 0"],
            }
        )
        joined = "\n".join(lines)
        self.assertIn("网络请求失败", joined, "账号异常没有被记进结果里")
        self.assertFalse(is_task_success(lines), "有账号整体失败，却仍被判成任务成功")

    def test_config_is_saved_even_when_an_account_crashes(self) -> None:
        """刷新过的 cookie_token 必须落盘，不能被异常跳过。"""
        _, _, saved = self._run({"A": ApiError("boom"), "B": []})
        self.assertTrue(saved, "run_tasks 在异常路径下没有 save_config，cookie 刷新结果会丢")

    def test_stop_event_still_breaks_out(self) -> None:
        """兜底不能把「手动停止」也一起吃掉。"""
        stop = threading.Event()
        stop.set()
        with (
            mock.patch.object(runner, "ApiClient", _fake_client),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            config = base_config()
            config["accounts"] = [{"name": "A"}]
            self.write_config(config)
            lines = runner.run_tasks(load_config(self.config_path), str(self.config_path), stop_event=stop)
        self.assertTrue(any("已停止" in line for line in lines), lines)


if __name__ == "__main__":
    unittest.main()
