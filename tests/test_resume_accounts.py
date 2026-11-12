# -*- coding: utf-8 -*-
"""账号级断点续跑。

问题：账号间隔要等 1~2 小时，这段窗口里注销 / 重启，剩下的账号就永久丢了。
`catch_up` 只能整轮重跑，重跑的第一个账号还得再等一轮 1~2 小时。
这里锁住两层行为：进度按账号落盘（scheduler），补跑时跳过已完成账号（runner）。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

from miyouqian.core.config import load_config
from miyouqian.core.http import ApiError
from miyouqian.service import runner
from miyouqian.service.scheduler import DailyScheduler
from tests.support import IsolatedConfigTest, base_config


def make_config(*, catch_up: bool = True, time: str = "09:00") -> dict:
    return {
        "schedule": {
            "enable": True,
            "time": time,
            "jitter_minutes": 0,
            "run_on_start": False,
            "catch_up": catch_up,
        }
    }


def _ctx(*args: object, **kwargs: object) -> "_Ctx":
    return _Ctx()


class _Ctx:
    def __enter__(self) -> object:
        return object()

    def __exit__(self, *exc: object) -> bool:
        return False


class ResumeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="myq-resume-")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = pathlib.Path(self._tmp.name) / "run_state.json"
        # 让 run_fn 卡住，测试读完状态再放行，避免后台线程跟断言抢时序
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)

    def make_scheduler(self, config: dict | None = None) -> DailyScheduler:
        return DailyScheduler(
            config or make_config(),
            lambda _stop: self.gate.wait(timeout=10) and [],
            lambda _message: None,
            state_path=self.state_path,
        )

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, **fields: object) -> None:
        self.state_path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")

    def drain(self, scheduler: DailyScheduler) -> None:
        self.gate.set()
        for _ in range(100):
            if not scheduler.status()["running"]:
                return
            time.sleep(0.02)
        self.fail("后台执行线程没有结束")

    def test_mark_account_done_is_persisted(self) -> None:
        scheduler = self.make_scheduler()
        scheduler._state_started()
        scheduler.mark_account_done("大号")
        scheduler.mark_account_done("大号")  # 重复标记不能写两遍
        self.assertEqual(self.read_state()["accounts_done"], ["大号"])

    def test_mark_account_done_ignores_empty_name(self) -> None:
        scheduler = self.make_scheduler()
        scheduler._state_started()
        scheduler.mark_account_done("  ")
        self.assertEqual(self.read_state()["accounts_done"], [])

    def test_new_day_clears_accounts_done(self) -> None:
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        self.write_state(date=yesterday, finished=False, accounts_done=["大号", "小号"])
        scheduler = self.make_scheduler()
        scheduler._state_started()
        self.assertEqual(self.read_state()["accounts_done"], [])

    def test_old_state_file_without_accounts_done_still_loads(self) -> None:
        # 向后兼容：升级前写下的 run_state.json 没有 accounts_done
        today = datetime.now().date().isoformat()
        self.write_state(date=today, started_at=f"{today}T08:00:00", finished=False)
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.assertEqual(scheduler.resume_skip_accounts(), set())
        scheduler.mark_account_done("大号")
        self.drain(scheduler)
        self.assertEqual(self.read_state()["accounts_done"], ["大号"])

    def test_resume_run_skips_accounts_done_today(self) -> None:
        today = datetime.now().date().isoformat()
        self.write_state(date=today, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.assertEqual(scheduler.resume_skip_accounts(), {"大号"})
        self.drain(scheduler)

    def test_manual_run_does_not_skip_anything(self) -> None:
        today = datetime.now().date().isoformat()
        self.write_state(date=today, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now()
        self.assertEqual(scheduler.resume_skip_accounts(), set())
        self.drain(scheduler)

    def test_resume_keeps_progress_in_state_file(self) -> None:
        # 补跑被打断第二次时，进度不能被 _state_started 清掉，否则又从第一个账号重来
        today = datetime.now().date().isoformat()
        self.write_state(date=today, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.drain(scheduler)
        self.assertEqual(self.read_state()["accounts_done"], ["大号"])

    def test_stale_progress_from_yesterday_is_ignored(self) -> None:
        yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
        self.write_state(date=yesterday, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.assertEqual(scheduler.resume_skip_accounts(), set())
        self.drain(scheduler)

    def test_scheduled_run_after_resume_does_not_skip(self) -> None:
        # 补跑完成后再来一次正常调度（或手动执行），不能还带着上一次的跳过集合
        today = datetime.now().date().isoformat()
        self.write_state(date=today, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.drain(scheduler)
        self.assertEqual(scheduler.resume_skip_accounts(), set())

    def test_interrupted_resume_is_still_caught_up(self) -> None:
        today = datetime.now().date().isoformat()
        base = (datetime.now() - timedelta(minutes=1)).strftime("%H:%M")
        if datetime.now().hour == 0 and datetime.now().minute == 0:
            self.skipTest("临近午夜，跨天会干扰用例")
        self.write_state(date=today, finished=False, accounts_done=["大号"])
        scheduler = self.make_scheduler(make_config(time=base))
        self.assertTrue(scheduler._should_catch_up())


class ResumeRunnerTest(IsolatedConfigTest):
    """runner 层：跳过已完成账号、间隔只作用于实际执行的账号、完成回调。"""

    def _run(
        self,
        behaviours: dict[str, object],
        *,
        skip: set[str] | None = None,
        gap_minutes: int = 20,
    ) -> tuple[list[str], list[str], list[float], list[str]]:
        config = base_config()
        config["accounts"] = [{"name": name} for name in behaviours]
        config["account_gap"] = {"enable": True, "min_minutes": gap_minutes, "max_minutes": gap_minutes}
        self.write_config(config)

        attempted: list[str] = []
        waited: list[float] = []
        done: list[str] = []

        class FakeGameCheckin:
            def __init__(self, client, config, account, emit=None):
                self.account = account

            def run(self, only_games=None):
                name = str(self.account.get("name"))
                attempted.append(name)
                outcome = behaviours[name]
                if isinstance(outcome, Exception):
                    raise outcome
                return []

        with (
            mock.patch.object(runner, "interruptible_sleep", lambda s, e=None: waited.append(s) or True),
            mock.patch.object(runner, "GameCheckin", FakeGameCheckin),
            mock.patch.object(runner, "ApiClient", _ctx),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            lines = runner.run_tasks(
                load_config(self.config_path),
                str(self.config_path),
                skip_accounts=skip,
                on_account_done=done.append,
            )
        return lines, attempted, waited, done

    def test_skipped_account_is_not_executed(self) -> None:
        _, attempted, _, done = self._run({"大号": [], "小号": []}, skip={"大号"})
        self.assertEqual(attempted, ["小号"])
        self.assertEqual(done, ["小号"])

    def test_first_executed_account_does_not_wait(self) -> None:
        # 补跑时第一个真正执行的账号不能再等 1~2 小时
        _, _, waited, _ = self._run({"大号": [], "小号": []}, skip={"大号"})
        self.assertEqual(waited, [], "补跑的第一个实际账号不该等待")

    def test_gap_applies_between_executed_accounts_only(self) -> None:
        _, attempted, waited, _ = self._run({"大号": [], "小号": []}, skip={"大号", "小号"}, gap_minutes=0)
        self.assertEqual(attempted, [])
        self.assertEqual(waited, [], "全部跳过时不应等待")
        _, _, waited2, _ = self._run({"A": [], "B": [], "C": []}, skip={"A"})
        self.assertEqual(waited2, [1200.0], "B、C 之间应该只等一次")

    def test_skip_notice_is_logged(self) -> None:
        lines, _, _, _ = self._run({"大号": [], "小号": []}, skip={"大号"})
        joined = "\n".join(lines)
        self.assertIn("账号 大号 今天已完成，跳过", joined)
        self.assertIn("账号 2/2: 小号", joined)

    def test_all_skipped_says_no_need_to_resume(self) -> None:
        lines, _, _, _ = self._run({"大号": [], "小号": []}, skip={"大号", "小号"})
        self.assertIn("# 今天所有账号均已完成，无需补跑", lines)

    def test_crashed_account_still_reports_done(self) -> None:
        # 坏账号也要计入进度，否则补跑会一直卡在它身上
        _, attempted, _, done = self._run({"大号": ApiError("超时"), "小号": []}, gap_minutes=0)
        self.assertEqual(attempted, ["大号", "小号"])
        self.assertEqual(done, ["大号", "小号"])

    def test_manual_run_reports_done_in_order(self) -> None:
        _, _, _, done = self._run({"A": [], "B": []}, gap_minutes=0)
        self.assertEqual(done, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
