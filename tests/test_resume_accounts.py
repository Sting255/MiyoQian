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


def make_config(*, catch_up: bool = True, time: str = "09:00", accounts: list[str] | None = None) -> dict:
    return {
        "schedule": {
            "enable": True,
            "time": time,
            "jitter_minutes": 0,
            "run_on_start": False,
            "catch_up": catch_up,
        },
        "accounts": [{"name": name} for name in (accounts or [])],
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
        scheduler.mark_account_done("1|大号")
        scheduler.mark_account_done("1|大号")  # 重复标记不能写两遍
        self.assertEqual(self.read_state()["accounts_done"], ["1|大号"])

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
        self.write_state(date=today, finished=False, accounts_done=["1|大号"])
        scheduler = self.make_scheduler()
        scheduler.run_now(resume=True)
        self.assertEqual(scheduler.resume_skip_accounts(), {"1|大号"})
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
        names: list[str] | None = None,
        skip: set[str] | None = None,
        gap_minutes: int = 20,
        ip_results: list[bool] | None = None,
        failed: list[str] | None = None,
    ) -> tuple[list[str], list[str], list[float], list[str]]:
        config = base_config()
        # names 允许重复：同名的两个账号要能真的配出两个来
        config["accounts"] = [{"name": name} for name in (names or list(behaviours))]
        config["account_gap"] = {"enable": True, "min_minutes": gap_minutes, "max_minutes": gap_minutes}
        self.write_config(config)

        attempted: list[str] = []
        waited: list[float] = []
        done: list[str] = []
        ip_calls: list[bool] = list(ip_results or [])

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
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: ip_calls.pop(0) if ip_calls else True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            lines = runner.run_tasks(
                load_config(self.config_path),
                str(self.config_path),
                skip_accounts=skip,
                on_account_done=done.append,
                on_account_failed=(failed.append if failed is not None else None),
            )
        return lines, attempted, waited, done

    def test_skipped_account_is_not_executed(self) -> None:
        _, attempted, _, done = self._run({"大号": [], "小号": []}, skip={"1|大号"})
        self.assertEqual(attempted, ["小号"])
        self.assertEqual(done, ["2|小号"])

    def test_old_name_only_progress_never_skips(self) -> None:
        # 旧格式（纯名字）的进度键对不上新键 -> 只能重跑，绝不能误跳过
        _, attempted, _, _ = self._run({"大号": [], "小号": []}, skip={"大号"}, gap_minutes=0)
        self.assertEqual(attempted, ["大号", "小号"])

    def test_same_name_accounts_do_not_shadow_each_other(self) -> None:
        # 账号名会被截断且不校验唯一，两个「小号」不能互相顶掉
        _, attempted, _, done = self._run(
            {"小号": []}, names=["小号", "小号"], skip={"1|小号"}, gap_minutes=0
        )
        self.assertEqual(attempted, ["小号"], "被跳过的只是第一个同名账号")
        self.assertEqual(done, ["2|小号"])

    def test_first_executed_account_does_not_wait(self) -> None:
        # 补跑时第一个真正执行的账号不能再等 1~2 小时
        _, _, waited, _ = self._run({"大号": [], "小号": []}, skip={"1|大号"})
        self.assertEqual(waited, [], "补跑的第一个实际账号不该等待")

    def test_ip_gate_does_not_take_an_execution_slot(self) -> None:
        # 被出口 IP 门禁挡下的账号没真执行，不能占名额让下一个账号白等
        _, attempted, waited, _ = self._run({"大号": [], "小号": []}, ip_results=[False, True])
        self.assertEqual(attempted, ["小号"])
        self.assertEqual(waited, [], "IP 门禁挡下的账号不该触发账号间隔等待")

    def test_ip_gate_skips_only_real_waits(self) -> None:
        _, attempted, waited, _ = self._run(
            {"A": [], "B": [], "C": []}, ip_results=[False, True, True]
        )
        self.assertEqual(attempted, ["B", "C"])
        self.assertEqual(waited, [1200.0], "B、C 之间只该等一次")

    def test_gap_applies_between_executed_accounts_only(self) -> None:
        _, attempted, waited, _ = self._run({"大号": [], "小号": []}, skip={"1|大号", "2|小号"}, gap_minutes=0)
        self.assertEqual(attempted, [])
        self.assertEqual(waited, [], "全部跳过时不应等待")
        _, _, waited2, _ = self._run({"A": [], "B": [], "C": []}, skip={"1|A"})
        self.assertEqual(waited2, [1200.0], "B、C 之间应该只等一次")

    def test_skip_notice_is_logged(self) -> None:
        lines, _, _, _ = self._run({"大号": [], "小号": []}, skip={"1|大号"})
        joined = "\n".join(lines)
        self.assertIn("账号 大号 今天已完成，跳过", joined)
        self.assertIn("账号 2/2: 小号", joined)

    def test_all_skipped_says_no_need_to_resume(self) -> None:
        lines, _, _, _ = self._run({"大号": [], "小号": []}, skip={"1|大号", "2|小号"})
        self.assertIn("# 今天所有账号均已完成，无需补跑", lines)

    def test_all_skipped_notice_is_not_printed_when_stopped(self) -> None:
        # 「已停止」和「全部已完成」不能同时出现
        stop = threading.Event()
        stop.set()
        config = base_config()
        config["accounts"] = [{"name": "大号"}]
        self.write_config(config)
        with (
            mock.patch.object(runner, "ApiClient", _ctx),
            mock.patch.object(runner, "ensure_mainland_ip", lambda *a, **k: True),
            mock.patch.object(runner, "save_config", lambda *a, **k: None),
        ):
            lines = runner.run_tasks(
                load_config(self.config_path),
                str(self.config_path),
                stop_event=stop,
                skip_accounts={"1|大号"},
            )
        self.assertTrue(any("已停止" in line for line in lines), lines)
        self.assertNotIn("# 今天所有账号均已完成，无需补跑", lines)

    def test_crashed_account_is_not_marked_done(self) -> None:
        # 崩掉的账号不算完成，下次启动还能再试一次
        failed: list[str] = []
        _, attempted, _, done = self._run(
            {"大号": ApiError("超时"), "小号": []}, gap_minutes=0, failed=failed
        )
        self.assertEqual(attempted, ["大号", "小号"])
        self.assertEqual(done, ["2|小号"])
        self.assertEqual(failed, ["1|大号"], "崩溃的账号要报给调度器，否则整轮会被当成已跑完")

    def test_crashed_account_is_retried_without_waiting(self) -> None:
        # 崩一次后，下一轮只补跑它，且它是第一个实际执行的账号 -> 不等待
        _, _, _, done = self._run({"大号": ApiError("超时"), "小号": []}, gap_minutes=0)
        self.assertEqual(done, ["2|小号"])
        failed: list[str] = []
        _, attempted, waited, _ = self._run({"大号": [], "小号": []}, skip={"2|小号"}, failed=failed)
        self.assertEqual(attempted, ["大号"])
        self.assertEqual(waited, [], "补试崩掉的账号不该再等 1~2 小时")
        self.assertEqual(failed, [])

    def test_ip_gate_all_blocked_does_not_claim_all_done(self) -> None:
        # 剩余账号全被出口 IP 挡下：一个都没签，不能打「全部已完成」
        failed: list[str] = []
        lines, attempted, _, done = self._run(
            {"大号": [], "小号": []}, skip={"1|大号"}, ip_results=[False], failed=failed
        )
        self.assertEqual(attempted, [])
        self.assertEqual(done, [])
        self.assertNotIn("# 今天所有账号均已完成，无需补跑", lines)
        self.assertTrue(any("出口 IP 未恢复" in line for line in lines), lines)

    def test_ip_gate_does_not_consume_the_attempt_budget(self) -> None:
        # 没建 ApiClient、没登录没验证码，不该记失败次数（否则代理自启会白吃两次预算）
        failed: list[str] = []
        self._run({"大号": [], "小号": []}, ip_results=[False, False], failed=failed)
        self.assertEqual(failed, [], "IP 挡下不能记成「尝试过」")

    def test_manual_run_reports_done_in_order(self) -> None:
        _, _, _, done = self._run({"A": [], "B": []}, gap_minutes=0)
        self.assertEqual(done, ["1|A", "2|B"])


class UnfinishedRunTest(unittest.TestCase):
    """没签成的那一轮不能算「今天已了结」，但同一账号每天最多试 2 次。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="myq-unfinished-")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = pathlib.Path(self._tmp.name) / "run_state.json"

    def make_scheduler(self, config: dict) -> DailyScheduler:
        return DailyScheduler(config, lambda _stop: [], lambda _message: None, state_path=self.state_path)

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_unfinished_account_keeps_today_open(self) -> None:
        scheduler = self.make_scheduler(make_config(accounts=["大号", "小号"]))

        def fake_run(_stop):
            scheduler.mark_account_done("1|大号")
            scheduler.mark_account_failed("2|小号")
            return []

        scheduler.run_fn = fake_run
        scheduler._run_once()

        state = self.read_state()
        self.assertFalse(state["finished"], "有账号没签成却记成今天已完成，补跑就永远不会触发")
        self.assertFalse(
            state.get("finished_at"),
            "没跑完不该留下 finished_at（网页会显示成上次执行成功）",
        )
        self.assertEqual(state["accounts_done"], ["1|大号"])
        self.assertEqual(state["accounts_attempts"], {"2|小号": 1})

    def test_all_done_marks_finished(self) -> None:
        scheduler = self.make_scheduler(make_config(accounts=["大号"]))

        def fake_run(_stop):
            scheduler.mark_account_done("1|大号")
            return []

        scheduler.run_fn = fake_run
        scheduler._run_once()

        state = self.read_state()
        self.assertTrue(state["finished"])
        self.assertTrue(state["finished_at"])

    def test_catch_up_fires_after_a_failure_then_stops_at_the_limit(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        today = now.date().isoformat()
        config = make_config(time=base, accounts=["大号", "小号"])

        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_done": ["1|大号"], "accounts_attempts": {"2|小号": 1}}),
            encoding="utf-8",
        )
        self.assertTrue(self.make_scheduler(config)._should_catch_up(), "崩过一次的账号应该还能补一次")

        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_done": ["1|大号"], "accounts_attempts": {"2|小号": 2}}),
            encoding="utf-8",
        )
        self.assertFalse(
            self.make_scheduler(config)._should_catch_up(), "试满 2 次后不该再因为它触发补跑"
        )

    def test_bad_attempt_value_is_treated_as_exhausted(self) -> None:
        # 状态文件被手改坏成字符串：当「已试满」，绝不能抛异常把调度线程弄死
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        today = now.date().isoformat()
        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_attempts": {"1|大号": "x"}}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(time=base, accounts=["大号"]))
        self.assertFalse(scheduler._should_catch_up(), "坏值不该被当成「还没试过」而反复补跑")
        self.assertEqual(scheduler._attempts_of({"1|大号": "x"}, "1|大号"), 2)

    def test_scheduler_thread_survives_a_broken_state_file(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        self.state_path.write_text(
            json.dumps({"date": now.date().isoformat(), "finished": False, "accounts_attempts": {"1|A": ["坏"]}}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(time=base, accounts=["A"]))
        scheduler.start()
        self.addCleanup(scheduler.stop)
        time.sleep(0.3)
        self.assertTrue(scheduler._thread and scheduler._thread.is_alive(), "调度线程被坏状态弄死了")
        self.assertEqual(scheduler.status()["last_error"], "")

    def test_bad_attempt_value_does_not_break_marking(self) -> None:
        now = datetime.now()
        today = now.date().isoformat()
        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_attempts": {"1|A": "x"}}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(accounts=["A"]))
        scheduler.mark_account_failed("1|A")
        self.assertEqual(self.read_state()["accounts_attempts"], {"1|A": 3})

    def test_mixed_budget_still_catches_up_for_the_untried_account(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        today = now.date().isoformat()
        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_done": [], "accounts_attempts": {"1|A": 2}}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(time=base, accounts=["A", "B"]))
        self.assertTrue(scheduler._should_catch_up(), "B 一次都没试过，不能因为 A 试满就不补")

    def test_failed_run_refreshes_last_run_from_started_at(self) -> None:
        # 上一轮没跑成（没写 finished_at）：界面的「上次执行」要跟着走，不能停在旧日子
        self.state_path.write_text(
            json.dumps(
                {
                    "date": "2026-11-10",
                    "started_at": "2026-11-10T08:00:00",
                    "finished_at": "2026-11-10T08:30:00",
                    "finished": True,
                }
            ),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(accounts=["A"]))
        self.assertEqual(scheduler.status()["last_run"], "2026-11-10T08:30:00")
        self.state_path.write_text(
            json.dumps({"date": "2026-11-12", "started_at": "2026-11-12T08:00:00", "finished": False}),
            encoding="utf-8",
        )
        fresh = self.make_scheduler(make_config(accounts=["A"]))
        self.assertEqual(fresh.status()["last_run"], "2026-11-12T08:00:00")

    def test_mixed_timezone_state_takes_the_later_one(self) -> None:
        # 带时区与朴素混用：以前直接 max() 会抛 TypeError，把 DailyScheduler.__init__ 搞挂
        self.state_path.write_text(
            json.dumps({"date": "2026-11-10", "started_at": "2026-11-10T08:00:00+08:00", "finished_at": "2026-11-10T08:30:00"}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(accounts=["A"]))
        self.assertEqual(scheduler.status()["last_run"], "2026-11-10T08:30:00")

    def test_all_accounts_done_only_sweeps(self) -> None:
        # 账号都跑完但没来得及写 finished（被杀在最后一步）：补跑只是空转一遍收尾
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        today = now.date().isoformat()
        self.state_path.write_text(
            json.dumps({"date": today, "finished": False, "accounts_done": ["1|大号", "2|小号"]}),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler(make_config(time=base, accounts=["大号", "小号"]))
        self.assertTrue(scheduler._should_catch_up())
        self.assertFalse(scheduler._attempts_exhausted(), "没有剩余账号就不该被当成「试满了」")


if __name__ == "__main__":
    unittest.main()
