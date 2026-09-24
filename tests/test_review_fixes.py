# -*- coding: utf-8 -*-
"""全项目复检（子代理②）发现的缺陷修复回归测试。

覆盖：调度器静默死亡、随机延后窗口内关掉计划仍执行、
login 的 data:null 崩溃、扫码超时无上下界、兑换计划索引错位、时间校准截断补偿、
以及启动脚本 / CI 的落地问题。
"""

from __future__ import annotations

import datetime as dt
import pathlib
import threading
import time
import unittest
from email.utils import parsedate_to_datetime
from unittest import mock

from miyouqian.auth import login as login_mod
from miyouqian.core.config import load_config, normalize_config, normalize_schedule_time
from miyouqian.service import exchange_scheduler as es
from miyouqian.service.exchange_scheduler import ExchangeScheduler, PlanWorker
from miyouqian.service.scheduler import DailyScheduler
from tests.support import IsolatedConfigTest, base_config, make_case_dir

PAST = int(time.time()) - 60


class ScheduleNormalizeTest(unittest.TestCase):
    """YAML 里 `time: 09:00` 不加引号会被解析成 540（六十进制），必须还原。"""

    def test_yaml_sexagesimal_integer_is_converted_back(self) -> None:
        self.assertEqual(normalize_schedule_time(540), "09:00")

    def test_common_string_forms(self) -> None:
        self.assertEqual(normalize_schedule_time("09:00"), "09:00")
        self.assertEqual(normalize_schedule_time("9:5"), "09:05")
        self.assertEqual(normalize_schedule_time(" 23:59 "), "23:59")

    def test_garbage_falls_back_to_default(self) -> None:
        for bad in ("", None, "abc", "25:00", "09:99", True, 99999):
            with self.subTest(value=bad):
                self.assertEqual(normalize_schedule_time(bad), "09:00")

    def test_normalize_schedule_sanitises_the_section(self) -> None:
        config = base_config()
        config["schedule"] = {"enable": "false", "time": 540, "jitter_minutes": "30分", "run_on_start": 1}
        normalize_config(config)
        schedule = config["schedule"]
        self.assertEqual(schedule["time"], "09:00")
        self.assertFalse(schedule["enable"])
        self.assertTrue(schedule["run_on_start"])
        self.assertEqual(schedule["jitter_minutes"], 45, "垃圾值应退回默认而不是抛错")

    def test_jitter_is_clamped(self) -> None:
        config = base_config()
        config["schedule"] = {"jitter_minutes": 99999}
        normalize_config(config)
        self.assertEqual(config["schedule"]["jitter_minutes"], 720)

    def test_config_loads_with_unquoted_time(self) -> None:
        """真实场景：config.yaml 里写 time: 09:00（没引号）也要能加载。"""
        config = base_config()
        path = self._write("schedule:\n  enable: true\n  time: 09:00\n  jitter_minutes: 30\n")
        loaded = load_config(path)
        self.assertEqual(loaded["schedule"]["time"], "09:00")

    def _write(self, text: str) -> pathlib.Path:
        folder = make_case_dir()
        target = folder / "config.yaml"
        target.write_text("enable: true\n" + text, encoding="utf-8")
        return target


class SchedulerResilienceTest(unittest.TestCase):
    """配置坏掉时调度线程不能死（否则自动签到永久失效，UI 还显示已启用）。"""

    def _scheduler(self, run_fn=None) -> DailyScheduler:
        from tests.support import base_config as _base

        config = _base()
        config["schedule"] = {"enable": True, "time": "09:00", "jitter_minutes": 0, "run_on_start": False}
        return DailyScheduler(config, run_fn or (lambda stop: []), lambda message: None)

    def test_loop_survives_a_failing_computation(self) -> None:
        scheduler = self._scheduler()
        with mock.patch.object(
            scheduler, "_compute_next_base_run", side_effect=ValueError("schedule.time 必须是 HH:MM 格式")
        ):
            scheduler.start()
            deadline = time.time() + 5
            while time.time() < deadline and not scheduler._last_error:
                time.sleep(0.05)
            alive = scheduler._thread is not None and scheduler._thread.is_alive()
            error = scheduler._last_error
            scheduler.stop()
        self.assertTrue(alive, "调度线程因为配置错误死掉了")
        self.assertIn("调度异常", error)

    def test_status_surfaces_the_error(self) -> None:
        scheduler = self._scheduler()
        scheduler._last_error = "调度异常: boom"
        self.assertEqual(scheduler.status()["last_error"], "调度异常: boom")

    def test_valid_schedule_unaffected(self) -> None:
        scheduler = self._scheduler()
        target = scheduler._compute_next_base_run({"time": "09:00"})
        self.assertEqual((target.hour, target.minute), (9, 0))


class JitterRecheckTest(unittest.TestCase):
    """到点后的随机延后窗口内关掉自动执行，就不该再跑。"""

    def _scheduler(self, config, calls: list) -> DailyScheduler:
        return DailyScheduler(config, lambda stop: calls.append(1) or [], lambda message: None)

    def test_disabling_during_jitter_cancels_the_run(self) -> None:
        config = base_config()
        config["schedule"] = {"enable": True, "time": "00:01", "jitter_minutes": 30, "run_on_start": False}
        calls: list = []
        scheduler = self._scheduler(config, calls)
        scheduler._next_run = dt.datetime.now() - dt.timedelta(seconds=1)

        def flip_and_allow(schedule, due_run):
            config["schedule"]["enable"] = False
            return True

        with mock.patch.object(scheduler, "_wait_jitter", flip_and_allow):
            scheduler._loop_once()

        self.assertEqual(calls, [], "延后期间已关闭自动执行，却仍然跑了")
        self.assertIsNone(scheduler._next_run)

    def test_enabled_still_runs(self) -> None:
        config = base_config()
        config["schedule"] = {"enable": True, "time": "00:01", "jitter_minutes": 0, "run_on_start": False}
        calls: list = []
        scheduler = self._scheduler(config, calls)
        scheduler._next_run = dt.datetime.now() - dt.timedelta(seconds=1)
        with mock.patch.object(scheduler, "_wait_jitter", lambda schedule, due: True):
            scheduler._loop_once()
        self.assertEqual(calls, [1])


class SkipNoticeTest(unittest.TestCase):
    """「今天的自动执行被跳过」必须留下日志，不能一声不响推到明天。"""

    def test_skip_logs_a_reason(self) -> None:
        config = base_config()
        config["schedule"] = {"enable": True, "time": "00:01", "jitter_minutes": 0, "run_on_start": False}
        messages: list[str] = []
        scheduler = DailyScheduler(config, lambda stop: [], messages.append)
        scheduler._next_run = dt.datetime.now() - dt.timedelta(seconds=1)
        scheduler._last_run = dt.datetime.now() + dt.timedelta(seconds=5)  # 比基点晚结束
        with mock.patch.object(scheduler, "_wait_jitter", lambda schedule, due: True):
            scheduler._loop_once()
        self.assertTrue(any("已跳过" in item for item in messages), messages)


class LoginRobustnessTest(unittest.TestCase):
    """login.py：data 为 null 不能崩；扫码超时必须有上下界。"""

    class FakeClient:
        def __init__(self, payload) -> None:
            self.payload = payload

        def post_json(self, url, **kwargs):
            return self.payload

        def get_json(self, url, **kwargs):
            return self.payload

    def _qr(self, payload) -> login_mod.QRLogin:
        return login_mod.QRLogin(self.FakeClient(payload), "device", "fp", "phone", "model")

    def test_null_data_does_not_crash(self) -> None:
        qr = self._qr({"retcode": 0, "data": None})
        self.assertEqual(qr._get_ltoken("stoken", "mid"), "")
        self.assertEqual(qr._get_cookie_token("stoken", "mid"), "")
        # fetch 在拿不到 url/ticket 时应当抛可读的 RuntimeError，
        # 而不是 "NoneType has no attribute get"
        with self.assertRaises(RuntimeError) as ctx:
            qr.fetch()
        self.assertNotIn("NoneType", str(ctx.exception))
        self.assertIn("url", str(ctx.exception))

    def test_refresh_cookie_token_handles_null_data(self) -> None:
        account = {"cookie": "x=1", "stuid": "1", "stoken": "s", "mid": "m"}
        result = login_mod.refresh_cookie_token(self.FakeClient({"retcode": 0, "data": None}), account)
        self.assertFalse(result)
        self.assertEqual(account["cookie"], "x=1")

    def test_timeout_is_clamped(self) -> None:
        self.assertEqual(login_mod.clamp_login_timeout(120), 120)
        self.assertEqual(login_mod.clamp_login_timeout(-5), 10)
        self.assertEqual(login_mod.clamp_login_timeout(10 ** 9), 600)
        self.assertEqual(login_mod.clamp_login_timeout("abc"), 120)
        self.assertEqual(login_mod.clamp_login_timeout(None), 120)

    def test_wait_uses_the_clamped_timeout(self) -> None:
        qr = self._qr({"retcode": 0, "data": {"status": "Init"}})
        with self.assertRaises(TimeoutError):
            qr.wait("ticket", timeout=-1)   # 负数被夹到 10 秒后仍会走完循环
        # 关键是不能因为负数立刻炸出别的异常；用 clamp 后最小 10 秒
        self.assertEqual(login_mod.MIN_LOGIN_TIMEOUT_SECONDS, 10)

    def test_terminal_qr_status_fails_fast(self) -> None:
        qr = self._qr({"retcode": 0, "data": {"status": "Expired"}})
        started = time.time()
        with self.assertRaises(RuntimeError) as ctx:
            qr.wait("ticket", timeout=30)
        self.assertLess(time.time() - started, 3, "过期状态应当立刻报错")
        self.assertIn("失效", str(ctx.exception))


class ExchangeWorkerUpdateTest(unittest.TestCase):
    """计划被删/重排后，保留的线程必须跟着更新下标 —— 否则会兑换到另一个计划。

    （上游的 tests/test_exchange_scheduler_reload.py 覆盖同一件事，
    这里是本分支自己的回归保护。）
    """

    def _scheduler(self, plans: list) -> ExchangeScheduler:
        from tests.support import base_config as _base

        config = _base()
        config["shop_exchange"] = {"enable": True, "plans": plans, "retry_seconds": 1, "retry_interval": 0.1}
        return ExchangeScheduler(config, lambda index: None, lambda message: None)

    def test_update_syncs_index_and_plan(self) -> None:
        worker = PlanWorker(
            index=6,
            plan={"goods_id": "old"},
            exchange_at=PAST,
            attempt_key="old:1",
            run_fn=lambda index: None,
            log_fn=lambda message: None,
        )
        updated = {"goods_id": "new"}
        worker.update(0, updated)
        self.assertEqual(worker.index, 0)
        self.assertIs(worker.plan, updated)

    def test_fired_worker_uses_the_updated_index(self) -> None:
        fired: list[int] = []
        worker = PlanWorker(
            index=6,
            plan={"goods_id": "B"},
            exchange_at=PAST,
            attempt_key="B:1",
            run_fn=fired.append,
            log_fn=lambda message: None,
        )
        worker.update(0, {"goods_id": "B"})
        with mock.patch.object(es, "sync_server_time_offset", lambda log: 0.0):
            worker._loop()
        self.assertEqual(fired, [0], "worker 用了过期的索引")

    def test_reload_updates_a_retained_worker_in_place(self) -> None:
        plans = [{} for _ in range(6)] + [
            {"goods_id": "T", "exchange_at": PAST + 99999, "enable": True, "auto": True, "account_index": 0}
        ]
        scheduler = self._scheduler(plans)
        with mock.patch.object(es.PlanWorker, "start", lambda self: None):
            scheduler._rebuild_workers()
        worker = next(iter(scheduler._workers.values()))
        self.assertEqual(worker.index, 6)

        updated = {"goods_id": "T", "exchange_at": PAST + 99999, "enable": True, "auto": True,
                   "account_index": 0, "goods_name": "改名了"}
        scheduler.reload({"shop_exchange": {"enable": True, "plans": [updated]}})

        self.assertIs(next(iter(scheduler._workers.values())), worker, "线程被重建了，应该原地复用")
        self.assertEqual(worker.index, 0, "重排后下标没同步")
        self.assertIs(worker.plan, updated)

    def test_deleted_plan_removes_its_worker(self) -> None:
        plans = [{"goods_id": "T", "exchange_at": PAST + 99999, "enable": True, "auto": True}]
        scheduler = self._scheduler(plans)
        with mock.patch.object(es.PlanWorker, "start", lambda self: None):
            scheduler._rebuild_workers()
        self.assertEqual(scheduler.status()["worker_count"], 1)
        scheduler.reload({"shop_exchange": {"enable": True, "plans": []}})
        self.assertEqual(scheduler.status()["worker_count"], 0)


class ServerTimeOffsetTest(unittest.TestCase):
    """HTTP Date 只有秒级精度，会把触发时间系统性推迟最多 1 秒。"""

    def test_truncation_is_compensated(self) -> None:
        class FakeResponse:
            headers = {"Date": "Wed, 23 Sep 2026 13:36:47 GMT"}

            def raise_for_status(self) -> None:
                pass

        server_ts = parsedate_to_datetime("Wed, 23 Sep 2026 13:36:47 GMT").timestamp()
        with mock.patch.object(es.httpx, "get", return_value=FakeResponse()), \
             mock.patch.object(es.time, "time", side_effect=[1000.0, 1000.2]):
            offset = es.sync_server_time_offset(lambda message: None)

        midpoint = 1000.1
        self.assertAlmostEqual(offset, (server_ts + 0.5) - midpoint, places=5)


class StartScriptTest(unittest.TestCase):
    """全新机器上 start.sh / start.bat 必须真的能找到 uv。"""

    def setUp(self) -> None:
        self.root = pathlib.Path(login_mod.__file__).resolve().parents[2]

    def test_start_sh_uses_the_current_uv_install_dir(self) -> None:
        text = (self.root / "scripts" / "start.sh").read_text(encoding="utf-8")
        self.assertIn(".local/bin", text, "uv 现在装在 ~/.local/bin")
        # 旧路径可以留作兜底，但必须排在前面，否则新装的 uv 找不到
        local = text.index(".local/bin")
        cargo = text.find(".cargo/bin")
        self.assertTrue(
            cargo == -1 or local < cargo,
            "~/.local/bin 必须排在旧的 ~/.cargo/bin 前面",
        )

    def test_start_sh_fails_loudly_when_uv_is_still_missing(self) -> None:
        text = (self.root / "scripts" / "start.sh").read_text(encoding="utf-8")
        self.assertIn("command -v uv", text.split("export PATH", 1)[-1],
                      "装完 uv 之后没有复查，失败会一路走到 uv venv 才报错")

    def test_start_bat_refreshes_path_after_install(self) -> None:
        text = (self.root / "scripts" / "start.bat").read_text(encoding="utf-8")
        self.assertIn(r"%USERPROFILE%\.local\bin", text, "装完 uv 后当前会话的 PATH 没补上")


class CiTimezoneTest(unittest.TestCase):
    """CI 不设 TZ 时推送里的时间是 UTC，比北京时间差 8 小时。"""

    def test_workflow_sets_timezone(self) -> None:
        root = pathlib.Path(login_mod.__file__).resolve().parents[2]
        text = (root / ".github" / "workflows" / "checkin.yml").read_text(encoding="utf-8")
        self.assertIn("TZ", text, "workflow 没有设置 TZ，推送时间会显示成 UTC")

    def test_workflow_timeout_fits_the_account_gap(self) -> None:
        """账号之间会随机等 1~2 小时，超时太小会把第二个账号掐掉。"""
        root = pathlib.Path(login_mod.__file__).resolve().parents[2]
        text = (root / ".github" / "workflows" / "checkin.yml").read_text(encoding="utf-8")
        self.assertIn("timeout-minutes: 350", text)


if __name__ == "__main__":
    unittest.main()
