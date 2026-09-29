# -*- coding: utf-8 -*-
"""每日调度的「补跑」与执行状态落盘。

背景（2026-09-29）：账号间隔要等 1~2 小时，这段窗口里注销 Windows 或重启，
剩下的账号就丢了，而且服务一重启内存里的 last_run 也没了，网页显示「上次执行：无」，
看起来像从来没自动跑过。这里锁住修复后的行为。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from datetime import datetime, timedelta

from miyouqian.core.config import DEFAULT_CONFIG, normalize_config
from miyouqian.service.scheduler import DailyScheduler


def make_config(*, catch_up: bool = True, time: str = "09:00", enable: bool = True) -> dict:
    return {
        "schedule": {
            "enable": enable,
            "time": time,
            "jitter_minutes": 0,
            "run_on_start": False,
            "catch_up": catch_up,
        }
    }


class CatchUpTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="myq-sched-")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = pathlib.Path(self._tmp.name) / "run_state.json"

    def make_scheduler(self, config: dict, run_fn=None) -> DailyScheduler:
        return DailyScheduler(
            config,
            run_fn or (lambda _stop: []),
            lambda _message: None,
            state_path=self.state_path,
        )

    def write_state(self, **fields) -> None:
        self.state_path.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")

    def test_catch_up_when_today_never_ran_and_time_passed(self) -> None:
        # 基准时间设在 1 分钟前，且状态文件为空 → 应该补跑
        now = datetime.now()
        base_time = now - timedelta(minutes=1)
        if base_time.date() != now.date():
            self.skipTest("临近午夜，跨天会干扰用例")
        scheduler = self.make_scheduler(make_config(time=base_time.strftime("%H:%M")))
        self.assertTrue(scheduler._should_catch_up())

    def test_no_catch_up_before_the_time(self) -> None:
        now = datetime.now()
        later = now + timedelta(minutes=30)
        if later.date() != now.date():
            self.skipTest("临近午夜，跨天会干扰用例")
        scheduler = self.make_scheduler(make_config(time=later.strftime("%H:%M")))
        self.assertFalse(scheduler._should_catch_up())

    def test_no_catch_up_when_today_already_finished(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        self.write_state(date=now.date().isoformat(), finished=True)
        scheduler = self.make_scheduler(make_config(time=base))
        self.assertFalse(scheduler._should_catch_up())

    def test_catch_up_when_today_was_interrupted(self) -> None:
        # 今天的记录存在但 finished=false（跑到一半被注销/重启打断）→ 还要补
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        self.write_state(date=now.date().isoformat(), finished=False, started_at=now.isoformat())
        scheduler = self.make_scheduler(make_config(time=base))
        self.assertTrue(scheduler._should_catch_up())

    def test_no_catch_up_when_disabled(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        scheduler = self.make_scheduler(make_config(catch_up=False, time=base))
        self.assertFalse(scheduler._should_catch_up())

    def test_no_catch_up_when_schedule_disabled(self) -> None:
        now = datetime.now()
        base = (now - timedelta(minutes=1)).strftime("%H:%M")
        scheduler = self.make_scheduler(make_config(time=base, enable=False))
        self.assertFalse(scheduler._should_catch_up())

    def test_last_run_is_restored_from_state(self) -> None:
        # 服务重启后，网页上的「上次执行」不能变空
        self.write_state(
            date="2026-09-29",
            started_at="2026-09-29T08:51:10",
            finished_at="2026-09-29T08:55:46",
            finished=True,
        )
        scheduler = self.make_scheduler(make_config())
        self.assertEqual(scheduler.status()["last_run"], "2026-09-29T08:55:46")

    def test_run_records_state(self) -> None:
        scheduler = self.make_scheduler(make_config())

        def fake_run(_stop):
            return ["done"]

        scheduler.run_fn = fake_run
        scheduler._run_once()

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(state["finished"])
        self.assertEqual(state["date"], datetime.now().date().isoformat())
        self.assertTrue(state["finished_at"])
        self.assertTrue(scheduler.status()["last_run"])

    def test_failed_run_is_not_marked_finished(self) -> None:
        scheduler = self.make_scheduler(make_config())

        def boom(_stop):
            raise RuntimeError("网络炸了")

        scheduler.run_fn = boom
        scheduler._run_once()

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertFalse(state["finished"])

    def test_manual_stop_counts_as_finished(self) -> None:
        # 用户主动点停止，不应该在下次启动时又被自动补跑
        scheduler = self.make_scheduler(make_config())

        def stop_immediately(stop_event):
            stop_event.set()
            return []

        scheduler.run_fn = stop_immediately
        scheduler._run_once()

        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertTrue(state["finished"])

    def test_broken_state_file_is_tolerated(self) -> None:
        self.state_path.write_text("{ 这不是 json", encoding="utf-8")
        scheduler = self.make_scheduler(make_config())
        self.assertEqual(scheduler.status()["last_run"], "")


class ScheduleConfigTest(unittest.TestCase):
    def test_catch_up_defaults_to_false(self) -> None:
        self.assertFalse(DEFAULT_CONFIG["schedule"]["catch_up"])

    def test_normalize_keeps_catch_up(self) -> None:
        config = {"schedule": {"enable": True, "time": "08:00", "catch_up": True}}
        normalize_config(config)
        self.assertTrue(config["schedule"]["catch_up"])

    def test_normalize_defaults_catch_up_to_false(self) -> None:
        config = {"schedule": {"enable": True, "time": "08:00"}}
        normalize_config(config)
        self.assertFalse(config["schedule"]["catch_up"])


if __name__ == "__main__":
    unittest.main()
