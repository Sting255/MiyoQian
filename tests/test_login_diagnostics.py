# -*- coding: utf-8 -*-
"""登录失败留下可定位的阶段，但不能保存半套凭证或泄漏 HTTP 查询参数。"""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from miyouqian import constants as c
from miyouqian.auth import webcookie
from miyouqian.core.http import ApiClient, ApiError
from miyouqian.core.ipcheck import IpLocation, LinkReport
from miyouqian.service import web as web_mod
from miyouqian.service.notifier import is_task_success
from miyouqian.tasks.bbs import BbsTasks
from miyouqian.tasks.games import GameCheckin
from tests.support import IsolatedConfigTest, base_config


class HttpErrorRedactionTest(unittest.TestCase):
    def _client(self, handler):
        client = ApiClient(jitter_range=None)
        client._client.close()
        client._client = httpx.Client(transport=httpx.MockTransport(handler))
        self.addCleanup(client.close)
        return client

    def test_http_status_error_keeps_endpoint_and_status_without_credentials(self):
        client = self._client(lambda request: httpx.Response(503, json={}))
        with self.assertRaises(ApiError) as raised:
            client.get_json(
                "https://user-secret:password-secret@example.test/token?ltoken=lt-secret#fragment-secret",
                params={"stoken": "st-secret"},
            )
        message = str(raised.exception)
        self.assertIn("https://example.test/token", message)
        self.assertIn("HTTP 503", message)
        self.assertNotIn("@", message)
        self.assertNotIn("?", message)
        for secret in ("user-secret", "password-secret", "lt-secret", "st-secret", "fragment-secret"):
            self.assertNotIn(secret, message)

    def test_transport_error_does_not_echo_exception_message(self):
        def fail(request):
            raise httpx.ConnectError("failed at " + str(request.url), request=request)

        client = self._client(fail)
        with self.assertRaises(ApiError) as raised:
            client.get_json("https://example.test/token", params={"stoken": "transport-secret"})
        self.assertIn("ConnectError", str(raised.exception))
        self.assertNotIn("transport-secret", str(raised.exception))

    def test_invalid_json_and_shape_do_not_echo_query_or_fragment(self):
        for content in (b"not JSON", b"[]"):
            with self.subTest(content=content):
                client = self._client(lambda request: httpx.Response(200, content=content))
                with self.assertRaises(ApiError) as raised:
                    client.get_json("https://example.test/token?stoken=json-secret#fragment-secret")
                self.assertIn("https://example.test/token", str(raised.exception))
                self.assertNotIn("json-secret", str(raised.exception))
                self.assertNotIn("fragment-secret", str(raised.exception))


class LoginDiagnosticsTest(IsolatedConfigTest):
    def setUp(self):
        super().setUp()
        config = base_config()
        config["accounts"] = [{"name": "A", "stuid": "100000001", "cookie": "old-cookie",
                               "stoken": "old-stoken", "mid": "old-mid"}]
        self.write_config(config)
        self.app = web_mod.WebApp(self.config_path)

    def test_failed_sms_verification_logs_stage_and_preserves_credentials(self):
        before = self.credentials_path.read_bytes()
        with mock.patch.object(web_mod, "ApiClient"), mock.patch.object(web_mod, "CaptchaLogin") as login:
            login.return_value.login_by_mobile_captcha.side_effect = RuntimeError("验证码不正确")
            with self.assertRaisesRegex(RuntimeError, "验证码不正确"):
                self.app.complete_login_captcha(0, "13800000000", "123456", "test-action")
        self.assertEqual(self.credentials_path.read_bytes(), before)
        self.assertFalse(self.app.login_state["running"])
        self.assertIn("校验短信验证码", self.app.logs[-1])

    def test_failed_token_exchange_logs_stage_without_saving_partial_login(self):
        before = self.credentials_path.read_bytes()
        with mock.patch.object(web_mod, "ApiClient"), mock.patch.object(web_mod, "CaptchaLogin") as login, \
                mock.patch.object(web_mod.webcookie, "fetch_web_cookies") as fetch:
            login.return_value.login_by_mobile_captcha.return_value = {
                "stuid": "100000001", "stoken": "partial-stoken", "mid": "partial-mid",
            }
            fetch.side_effect = webcookie.WebCookieError("确认登录失败: retcode=-3101 message=拒绝")
            with self.assertRaisesRegex(RuntimeError, "-3101"):
                self.app.complete_login_captcha(0, "13800000000", "123456", "test-action")
        self.assertEqual(self.credentials_path.read_bytes(), before)
        self.assertEqual(self.app.config["accounts"][0]["stoken"], "old-stoken")
        self.assertFalse(self.app.login_state["running"])
        self.assertIn("换取完整登录凭证", self.app.logs[-1])
        for secret in ("partial-stoken", "partial-mid", "13800000000", "123456"):
            self.assertNotIn(secret, "\n".join(self.app.logs))

    def test_successful_login_saves_web_cookies(self):
        web = {"cookie_token_v2": "WEB-CT", "ltoken_v2": "WEB-LT", "account_mid_v2": "new-mid"}
        with mock.patch.object(web_mod, "ApiClient"), mock.patch.object(web_mod, "CaptchaLogin") as login, \
                mock.patch.object(web_mod.webcookie, "fetch_web_cookies", return_value=web):
            login.return_value.login_by_mobile_captcha.return_value = {
                "stuid": "100000001", "stoken": "new-stoken", "mid": "new-mid",
            }
            self.app.complete_login_captcha(0, "13800000000", "123456", "test-action")
        account = self.app.config["accounts"][0]
        self.assertEqual(account["stoken"], "new-stoken")
        self.assertIn("cookie_token_v2=WEB-CT", account["cookie"])
        self.assertIn("ltoken=WEB-LT", account["cookie"])
        self.assertIn("凭证已保存", self.app.logs[-1])

    def test_web_ip_log_uses_successful_fallback_location(self):
        report = LinkReport(
            domestic=IpLocation(error="domestic unavailable"),
            overseas=IpLocation(ip="203.0.113.8", country_code="CN", region="中国 江苏 南京"),
        )
        with mock.patch.object(web_mod, "probe_links", return_value=report):
            result = self.app.check_ip()
        self.assertTrue(result["mainland"])
        self.assertIn("203.0.113.8", self.app.logs[-1])
        self.assertNotIn("未知 IP", self.app.logs[-1])

    def test_web_split_ip_log_does_not_claim_proxy_or_pause(self):
        report = LinkReport(
            domestic=IpLocation(ip="203.0.113.7", country_code="CN"),
            overseas=IpLocation(ip="203.0.113.8", country_code="CN"),
        )
        with mock.patch.object(web_mod, "probe_links", return_value=report):
            result = self.app.check_ip()
        self.assertTrue(result["mainland"])
        self.assertFalse(result["blocked"])
        self.assertIn("出口不同", self.app.logs[-1])
        self.assertNotIn("分流代理", self.app.logs[-1])
        self.assertNotIn("已暂停", self.app.logs[-1])


class RefreshFailureDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(
            webcookie, "fetch_web_cookies",
            side_effect=webcookie.WebCookieError("确认扫码失败: retcode=-464 message=请使用最新版本产品/链接"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    class DeniedClient:
        def get_json(self, url, **kwargs):
            if url in (c.ACCOUNT_ROLES_URL, c.BBS_TASKS_URL):
                return {"retcode": -100, "message": "登录失效", "data": None}
            return {"retcode": -464, "message": "请使用最新版本产品/链接以获得更佳体验", "data": None}

    def account(self):
        return {"name": "A", "stuid": "100000001", "cookie": "old-cookie",
                "stoken": "v2_test-stoken", "mid": "test-mid"}

    def test_game_failure_preserves_upstream_refresh_rejection(self):
        task = GameCheckin(self.DeniedClient(), base_config(), self.account())
        roles, reason = task._get_roles(c.GAMES["genshin"])
        self.assertEqual(roles, [])
        self.assertIn("自动刷新失败", reason)
        self.assertIn("-464", reason)
        self.assertNotIn("v2_test-stoken", reason)

    def test_community_failure_preserves_upstream_refresh_rejection(self):
        task = BbsTasks(self.DeniedClient(), base_config(), self.account())
        lines = task.run()
        self.assertIn("任务状态获取失败，请检查 cookie/stoken", lines)
        self.assertIn("-464", "\n".join(lines))
        self.assertNotIn("v2_test-stoken", "\n".join(lines))

    def test_failed_final_community_state_counts_as_task_failure(self):
        state = {
            "can_get_points": 1,
            "already_received_points": 0,
            "total_points": 100,
            "states": [
                {"mission_id": mission_id, "is_get_award": True}
                for mission_id in (58, 59, 60, 61)
            ],
        }

        class FinalStateDeniedClient:
            def __init__(self):
                self.responses = [
                    {"retcode": 0, "data": state},
                    {"retcode": -100, "message": "登录失效"},
                ]

            def get_json(self, url, **kwargs):
                return self.responses.pop(0)

        def fail_refresh(_client, _account, *, on_error=None, **_kwargs):
            if on_error:
                on_error("retcode=-464 官方限制")
            return False

        task = BbsTasks(FinalStateDeniedClient(), base_config(), self.account())
        with mock.patch("miyouqian.tasks.bbs.refresh_cookie_token", side_effect=fail_refresh):
            lines = task.run()
        text = "\n".join(lines)
        self.assertIn("最终状态获取失败，本轮按失败处理", text)
        self.assertIn("失败 1", text)
        self.assertFalse(is_task_success(lines))
        self.assertIn("retcode=-464", text)
        self.assertNotIn("v2_test-stoken", text)


if __name__ == "__main__":
    unittest.main()
