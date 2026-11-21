# -*- coding: utf-8 -*-
"""换证/续期失败不得覆盖凭证，失败原因必须脱敏保留；网页凭证链路按协议走完四步。"""

from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

import httpx

from miyouqian import constants as c
from miyouqian.auth import login as login_mod
from miyouqian.auth import webcookie
from miyouqian.auth.login import PassportLogin, refresh_cookie_token
from miyouqian.tasks import bbs, games
from tests.support import base_config


class AdditionalTokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = mock.Mock()
        self.login = PassportLogin(self.client, "TEST-DEVICE", "TEST-FP")

    def test_empty_ltoken_stops_before_cookie_exchange(self) -> None:
        for payload in (None, {}, [], {"ltoken": " "}, {"ltoken": {"bad": "shape"}}):
            with self.subTest(payload=payload):
                self.client.reset_mock()
                self.client.get_json.return_value = {"retcode": 0, "data": payload}
                with self.assertRaisesRegex(RuntimeError, "ltoken.*未返回有效凭证"):
                    self.login.get_additional_tokens("TEST-STOKEN", "TEST-MID")
                self.assertEqual(self.client.get_json.call_count, 1)

    def test_upstream_error_cannot_echo_stoken_or_mid(self) -> None:
        stoken, mid = "TEST-STOKEN-SECRET", "TEST-MID-SECRET"
        self.client.get_json.return_value = {"retcode": 1002, "message": f"rejected {stoken} {mid}"}
        with self.assertRaises(RuntimeError) as raised:
            self.login.get_additional_tokens(stoken, mid)
        self.assertIn("retcode=1002", str(raised.exception))
        self.assertNotIn(stoken, str(raised.exception))
        self.assertNotIn(mid, str(raised.exception))


WEB = {
    "account_id": "100000001", "account_id_v2": "100000001", "account_mid_v2": "NEW-MID",
    "cookie_token_v2": "NEW-COOKIE", "ltmid_v2": "NEW-MID", "ltoken_v2": "NEW-LTOKEN",
    "ltuid": "100000001", "ltuid_v2": "100000001",
}


class CookieRefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = mock.Mock()
        self.account = {
            "stuid": "100000001",
            "stoken": "TEST-STOKEN-SECRET",
            "mid": "TEST-MID-SECRET",
            "cookie": "ltuid=100000001; cookie_token=OLD-COOKIE-SECRET; ltoken=OLD-LTOKEN-SECRET",
        }
        self.original = copy.deepcopy(self.account)
        self.messages: list[str] = []
        patcher = mock.patch.object(login_mod.webcookie, "fetch_web_cookies")
        self.fetch = patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_renews_web_cookie_with_the_login_device(self) -> None:
        self.fetch.return_value = dict(WEB)
        device = {"id": "SAVED-DEVICE", "fp": "SAVED-FP"}
        self.assertTrue(refresh_cookie_token(self.client, self.account, device=device, on_error=self.messages.append))
        self.fetch.assert_called_once_with("100000001", "TEST-STOKEN-SECRET", "TEST-MID-SECRET", device)
        for part in ("cookie_token=NEW-COOKIE", "cookie_token_v2=NEW-COOKIE", "ltoken=NEW-LTOKEN",
                     "ltoken_v2=NEW-LTOKEN", "account_mid_v2=NEW-MID"):
            self.assertIn(part, self.account["cookie"])
        self.assertNotIn("OLD-", self.account["cookie"])
        self.assertEqual(self.account["stoken"], self.original["stoken"])
        self.client.get_json.assert_not_called()
        self.assertEqual(self.messages, [])

    def test_failure_preserves_cookie_and_reports_reason(self) -> None:
        self.fetch.side_effect = webcookie.WebCookieError("确认登录失败: retcode=-464 message=请使用最新版本")
        self.assertFalse(refresh_cookie_token(self.client, self.account, on_error=self.messages.append))
        self.assertEqual(self.account, self.original)
        self.assertIn("retcode=-464", self.messages[0])
        self.assertIn("自动续期失败", self.messages[0])

    def test_failure_diagnostics_do_not_leak_secrets(self) -> None:
        self.fetch.side_effect = webcookie.WebCookieError(" ".join(self.account.values()))
        self.assertFalse(refresh_cookie_token(self.client, self.account, on_error=self.messages.append))
        text = self.messages[0]
        for value in (self.account["stoken"], self.account["mid"], "OLD-COOKIE-SECRET", "OLD-LTOKEN-SECRET"):
            self.assertNotIn(value, text)

    def test_unexpected_exception_does_not_echo_credentials(self) -> None:
        self.fetch.side_effect = OSError("request?stoken=" + self.account["stoken"])
        self.assertFalse(refresh_cookie_token(self.client, self.account, on_error=self.messages.append))
        self.assertIn("OSError", self.messages[0])
        self.assertNotIn(self.account["stoken"], self.messages[0])
        self.assertEqual(self.account, self.original)

    def test_missing_credentials_do_not_send_requests(self) -> None:
        self.assertFalse(refresh_cookie_token(self.client, {}, on_error=self.messages.append))
        self.fetch.assert_not_called()
        self.assertIn("缺少有效", self.messages[0])

    def test_renewal_updates_all_aliases_and_preserves_unrelated_cookies(self) -> None:
        self.account["cookie"] += "; ltoken_v2=STALE; cookie_token_v2=STALE; ltoken=STALE; theme=dark"
        self.fetch.return_value = dict(WEB)
        self.assertTrue(refresh_cookie_token(self.client, self.account))
        self.assertNotIn("STALE", self.account["cookie"])
        self.assertIn("theme=dark", self.account["cookie"])


class WebCookieFlowTest(unittest.TestCase):
    """网页扫码登录链路：stoken 以 App 身份确认网页二维码，网页查询结果的 Set-Cookie 即网页凭证。"""

    def _run(self, handler):
        real_client = httpx.Client

        def make_client(**kwargs):
            return real_client(transport=httpx.MockTransport(handler), **kwargs)

        with mock.patch.object(webcookie.httpx, "Client", side_effect=make_client), \
                mock.patch.object(webcookie.time, "sleep"):
            return webcookie.fetch_web_cookies("100000001", "v2_SECRET-STOKEN", "SECRET-MID",
                                               {"id": "DEV", "fp": "FP"}, poll_seconds=5)

    def test_confirmed_web_login_returns_web_cookies(self) -> None:
        seen: list[tuple[str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            body = json.loads(request.content or b"{}")
            seen.append((path, body))
            if path.endswith("/web/createQRLogin"):
                return httpx.Response(200, json={"retcode": 0, "data": {
                    "ticket": "TK", "url": "https://user.mihoyo.com/qr?tk=TK&token_types=4&expire=1"}})
            if path.endswith("/app/scanQRLogin") or path.endswith("/app/confirmQRLogin"):
                self.assertIn("stoken=v2_SECRET-STOKEN", request.headers["cookie"])
                self.assertEqual(request.headers["x-rpc-client_type"], "2")
                return httpx.Response(200, json={"retcode": 0, "data": {}})
            if path.endswith("/web/queryQRLoginStatus"):
                headers = [("set-cookie", f"{k}={v}; Path=/; Domain=.mihoyo.com") for k, v in WEB.items()]
                return httpx.Response(200, headers=headers, json={"retcode": 0, "data": {"status": "Confirmed"}})
            return httpx.Response(404, json={})

        result = self._run(handler)
        self.assertEqual(result["cookie_token_v2"], "NEW-COOKIE")
        self.assertEqual(result["ltoken_v2"], "NEW-LTOKEN")
        steps = [p.rsplit("/", 1)[-1] for p, _ in seen]
        self.assertEqual(steps, ["createQRLogin", "scanQRLogin", "confirmQRLogin", "queryQRLoginStatus"])
        self.assertEqual(seen[1][1], {"ticket": "TK", "token_types": [4]})

    def test_rejected_confirm_raises_without_secrets(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/web/createQRLogin"):
                return httpx.Response(200, json={"retcode": 0, "data": {"ticket": "TK", "url": "https://x/?token_types=4"}})
            return httpx.Response(200, json={"retcode": -3101, "message": "rejected v2_SECRET-STOKEN"})

        with self.assertRaises(webcookie.WebCookieError) as raised:
            self._run(handler)
        self.assertIn("retcode=-3101", str(raised.exception))

    def test_missing_web_tokens_is_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/web/createQRLogin"):
                return httpx.Response(200, json={"retcode": 0, "data": {"ticket": "TK", "url": "https://x/?token_types=4"}})
            return httpx.Response(200, json={"retcode": 0, "data": {"status": "Confirmed"}})

        with self.assertRaisesRegex(webcookie.WebCookieError, "cookie_token_v2"):
            self._run(handler)

    def test_transport_error_is_wrapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom v2_SECRET-STOKEN", request=request)

        with self.assertRaises(webcookie.WebCookieError) as raised:
            self._run(handler)
        self.assertIn("ConnectError", str(raised.exception))
        self.assertNotIn("SECRET", str(raised.exception))


class TaskRenewalTest(unittest.TestCase):
    def test_game_run_refreshes_web_cookie_before_signing(self) -> None:
        account = {"stuid": "100000001", "stoken": "v2_TEST", "mid": "TEST-MID", "cookie": "ltoken=OLD"}
        client = mock.Mock()
        seen_cookies: list[str] = []

        def request(url, **kwargs):
            seen_cookies.append(kwargs["headers"]["Cookie"])
            if url == c.ACCOUNT_ROLES_URL:
                return {"retcode": 0, "data": {"list": []}}
            return {"retcode": 0, "data": {}}

        client.get_json.side_effect = request
        config = base_config()
        config["games"]["enabled"] = ["genshin"]
        with mock.patch.object(games, "refresh_cookie_token", wraps=None) as refresh, \
                mock.patch.object(games, "sleep_by_config"):
            def fake_refresh(_client, acc, **_kwargs):
                acc["cookie"] = "cookie_token_v2=NEW; ltoken_v2=NEW"
                return True
            refresh.side_effect = fake_refresh
            games.GameCheckin(client, config, account).run()
        refresh.assert_called_once()
        self.assertTrue(seen_cookies and all("NEW" in item for item in seen_cookies))

    def test_failed_renewal_is_not_repeated_for_every_game(self) -> None:
        task = games.GameCheckin(mock.Mock(), base_config(), {})
        task.client.get_json.return_value = {"retcode": -100, "message": "登录失效"}
        with mock.patch.object(games, "refresh_cookie_token", return_value=False) as refresh:
            task._get_roles(c.GAMES["genshin"])
            task._get_roles(c.GAMES["starrail"])
        refresh.assert_called_once()

    def test_failed_community_renewal_is_not_repeated_for_final_state_read(self) -> None:
        task = bbs.BbsTasks(mock.Mock(), base_config(), {})
        task.client.get_json.return_value = {"retcode": -101, "message": "登录失效"}
        with mock.patch.object(bbs, "refresh_cookie_token", return_value=False) as refresh:
            task._task_state()
            task._task_state()
        refresh.assert_called_once()


if __name__ == "__main__":
    unittest.main()
