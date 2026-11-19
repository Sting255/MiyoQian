# -*- coding: utf-8 -*-
"""「仅在出错时推送」的可用性验证 + 4 项附带修复的回归测试。

验证目标（error_only 是已有功能，不重做）：
  - 成功不推、失败推；
  - 逐账号推送和整轮总结走同一套判断；
  - 「测试推送」必须绕过 error_only（否则点了什么都收不到）；
  - cookie/ltoken 失效这类任务级错误要显示成红点和失败项；
  - 调度器异常要推送且节流；
  - run_state.json 不残留上一轮的 finished_at；
  - ltoken/cookie_token 不再明文进 config.yaml / 日志 / /api/config。
"""

from __future__ import annotations

import json
import pathlib
import tempfile
import unittest
from datetime import datetime
from unittest import mock

from miyouqian.core.config import load_config
from miyouqian.service import notifier, runner, web as web_mod
from miyouqian.service import scheduler as scheduler_mod
from miyouqian.service.scheduler import DailyScheduler
from miyouqian.service.web import WebApp, is_sensitive_config_path
from tests.support import IsolatedConfigTest, base_config

SECRET_LTOKEN = "LTOKEN-SECRET-VALUE"
SECRET_COOKIE_TOKEN = "COOKIE-TOKEN-SECRET-VALUE"


def push_config(*, error_only: bool) -> dict:
    config = base_config()
    config["push"] = {
        "enable": True,
        "error_only": error_only,
        "per_account": False,
        "channels": [{"provider": "pushplus", "enable": True, "token": "T"}],
    }
    return config


class ErrorOnlyGateTest(unittest.TestCase):
    def test_success_is_suppressed(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            result = notifier.send_push(push_config(error_only=True), "标题", "正文", success=True)
        self.assertEqual(result, "")
        sender.assert_not_called()

    def test_failure_is_sent(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            result = notifier.send_push(push_config(error_only=True), "标题", "正文", success=False)
        self.assertTrue(result.startswith("推送结果"), result)
        sender.assert_called_once()

    def test_force_bypasses_error_only(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            result = notifier.send_push(
                push_config(error_only=True), "标题", "正文", success=True, force=True
            )
        self.assertTrue(result.startswith("推送结果"), result)
        sender.assert_called_once()

    def test_switch_off_still_sends_success(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            notifier.send_push(push_config(error_only=False), "标题", "正文", success=True)
        sender.assert_called_once()


class PushRunResultTest(unittest.TestCase):
    SUCCESS_LINES = ["# 账号 1/1: 甲", "游戏社区签到汇总：成功 3，失败 0，跳过 0"]
    FAILED_LINES = ["# 账号 1/1: 甲", "米游币失败项：大别野社区签到触发验证码"]

    def test_successful_run_is_not_pushed(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            success, result = notifier.push_run_result(push_config(error_only=True), self.SUCCESS_LINES)
        self.assertTrue(success)
        self.assertEqual(result, "")
        sender.assert_not_called()

    def test_failed_run_is_pushed(self) -> None:
        with mock.patch.object(notifier, "_send") as sender:
            success, result = notifier.push_run_result(push_config(error_only=True), self.FAILED_LINES)
        self.assertFalse(success)
        self.assertTrue(result.startswith("推送结果"), result)
        sender.assert_called_once()


class PerAccountPushTest(unittest.TestCase):
    def test_successful_account_is_not_pushed(self) -> None:
        added: list[str] = []
        with mock.patch.object(notifier, "_send") as sender:
            runner._push_account_result(
                push_config(error_only=True),
                added.append,
                {"name": "甲"},
                ["游戏社区签到汇总：成功 1，失败 0，跳过 0"],
            )
        sender.assert_not_called()
        self.assertEqual(added, [])

    def test_failed_account_is_pushed(self) -> None:
        added: list[str] = []
        with mock.patch.object(notifier, "_send") as sender:
            runner._push_account_result(
                push_config(error_only=True),
                added.append,
                {"name": "甲"},
                ["米游币失败项：大别野社区签到触发验证码"],
            )
        sender.assert_called_once()
        self.assertTrue(added and "已推送" in added[0], added)


class TaskStateErrorVisibilityTest(unittest.TestCase):
    LINES = [
        "# 账号 1/2: 甲",
        "== 米游币社区任务 ==",
        "正在获取米游币任务状态",
        "任务状态获取失败，请检查 cookie/stoken",
    ]

    def test_expired_cookie_is_a_failure(self) -> None:
        self.assertFalse(notifier.is_task_success(self.LINES))

    def test_card_is_red_and_reason_is_visible(self) -> None:
        summary = notifier.build_structured_summary(self.LINES, False)
        html = notifier.build_account_sections_html(summary)
        self.assertIn("#b83b4b", html, "米游币任务失败时账号卡片仍然是绿点")
        self.assertIn("米游币任务失败", html)
        self.assertIn("cookie", summary["overview"]["failed_block_text"])
        self.assertIn("失败 1", notifier.build_push_title(self.LINES, False))


class TestPushButtonTest(IsolatedConfigTest):
    def test_test_push_ignores_error_only(self) -> None:
        config = push_config(error_only=True)
        self.write_config(config)
        app = WebApp(self.config_path)
        with mock.patch.object(
            web_mod, "send_push", return_value="推送结果: pushplus: 成功"
        ) as sender:
            result = app.test_push_channels()
        self.assertIn("成功", result)
        sender.assert_called_once()
        self.assertTrue(sender.call_args.kwargs.get("force"), "测试推送没有绕过 error_only")


class SchedulerErrorNoticeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="myq-push-")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = pathlib.Path(self._tmp.name) / "run_state.json"

    def make_scheduler(self) -> DailyScheduler:
        return DailyScheduler(
            push_config(error_only=True),
            lambda _stop: [],
            lambda _message: None,
            state_path=self.state_path,
        )

    def test_error_push_is_throttled(self) -> None:
        scheduler = self.make_scheduler()
        with mock.patch.object(scheduler_mod, "send_push") as sender:
            scheduler._notify_error("调度异常: boom")
            scheduler._notify_error("调度异常: boom")
            self.assertEqual(sender.call_count, 1, "同一条调度异常被重复推送")
            scheduler._notify_error("调度异常: 另一条")
            self.assertEqual(sender.call_count, 2)
        self.assertFalse(sender.call_args.kwargs.get("success", True))


class RunStateTimestampTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="myq-state-")
        self.addCleanup(self._tmp.cleanup)
        self.state_path = pathlib.Path(self._tmp.name) / "run_state.json"

    def make_scheduler(self) -> DailyScheduler:
        return DailyScheduler({}, lambda _stop: [], lambda _message: None, state_path=self.state_path)

    def read_state(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def test_start_clears_stale_finished_at(self) -> None:
        today = datetime.now().date().isoformat()
        self.state_path.write_text(
            json.dumps(
                {
                    "date": today,
                    "started_at": f"{today}T08:00:00",
                    "finished": True,
                    "finished_at": f"{today}T10:48:23",
                }
            ),
            encoding="utf-8",
        )
        scheduler = self.make_scheduler()
        scheduler._state_started()
        state = self.read_state()
        self.assertFalse(state["finished"])
        self.assertIsNone(state.get("finished_at"), "新一轮开始时残留了上一轮的完成时间")

    def test_failed_finish_clears_finished_at(self) -> None:
        scheduler = self.make_scheduler()
        scheduler._state_started()
        scheduler._state_finished(False)
        self.assertIsNone(self.read_state().get("finished_at"))


class CredentialHygieneTest(IsolatedConfigTest):
    def make_config(self) -> dict:
        config = base_config()
        config["accounts"] = [
            {
                "name": "A",
                "stuid": "100000001",
                "cookie": "COOKIE-LIVE-VALUE",
                "stoken": "STOKEN-LIVE-VALUE",
                "mid": "MID-LIVE-VALUE",
                "ltoken": SECRET_LTOKEN,
                "cookie_token": SECRET_COOKIE_TOKEN,
            }
        ]
        return config

    def test_secrets_are_moved_out_of_config_yaml(self) -> None:
        self.write_config(self.make_config())
        config_text = self.config_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_LTOKEN, config_text)
        self.assertNotIn(SECRET_COOKIE_TOKEN, config_text)
        credentials_text = self.credentials_path.read_text(encoding="utf-8")
        self.assertIn(SECRET_LTOKEN, credentials_text)
        self.assertIn(SECRET_COOKIE_TOKEN, credentials_text)
        # 重新加载后凭证不能丢
        reloaded = load_config(self.config_path)
        self.assertEqual(reloaded["accounts"][0]["ltoken"], SECRET_LTOKEN)

    def test_api_masks_and_save_keeps_them(self) -> None:
        self.write_config(self.make_config())
        app = WebApp(self.config_path)
        payload = app.get_config()
        blob = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(SECRET_LTOKEN, blob)
        self.assertNotIn(SECRET_COOKIE_TOKEN, blob)

        app.set_config(payload)
        self.assertEqual(app.config["accounts"][0]["ltoken"], SECRET_LTOKEN)
        self.assertEqual(app.config["accounts"][0]["cookie_token"], SECRET_COOKIE_TOKEN)

    def test_ltoken_is_masked_in_config_logs(self) -> None:
        self.assertTrue(is_sensitive_config_path("accounts[0].ltoken"))
        self.assertTrue(is_sensitive_config_path("accounts[0].cookie_token"))


if __name__ == "__main__":
    unittest.main()
