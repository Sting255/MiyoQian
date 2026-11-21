# -*- coding: utf-8 -*-
"""Passport 换证签名必须独立于社区签名，凭证只放 Cookie。

协议依据（国服 GET，而非海外 getBySToken）：
https://github.com/seriaati/genshin.py/blob/master/genshin/client/manager/cookie.py
https://github.com/seriaati/genshin.py/blob/master/genshin/utility/ds.py
"""

from __future__ import annotations

import hashlib
import unittest
from unittest import mock

import httpx

from miyouqian import constants as c
from miyouqian.auth.login import PassportLogin
from miyouqian.core import crypto
from miyouqian.core.http import ApiClient


class PassportSignatureTest(unittest.TestCase):
    def test_auth_signature_uses_app_login_salt_without_body_or_query(self) -> None:
        with mock.patch.object(crypto.time, "time", return_value=1700000000), mock.patch.object(
            crypto.random, "choices", return_value=list("aBcDeF")
        ):
            signature = crypto.ds_passport_auth()
        digest = hashlib.md5(
            b"salt=IZPgfb0dRPtBeLuFkdDznSZ6f4wWt6y2&t=1700000000&r=aBcDeF"
        ).hexdigest()
        self.assertEqual(signature, f"1700000000,aBcDeF,{digest}")


class PassportProtocolTest(unittest.TestCase):
    def test_auth_requests_use_cookie_and_app_signature_without_stoken_query(self) -> None:
        requests: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            field = "ltoken" if request.url.path.endswith("getLTokenBySToken") else "cookie_token"
            return httpx.Response(200, json={"retcode": 0, "data": {field: f"TEST-{field}"}})

        with ApiClient(jitter_range=None) as client:
            client._client.close()
            client._client = httpx.Client(transport=httpx.MockTransport(handle))
            login = PassportLogin(client, "TEST-DEVICE", "TEST-FP")
            with mock.patch.object(crypto, "ds_passport_auth", return_value="TEST-PASSPORT-SIGNATURE"):
                result = login.get_additional_tokens("TEST-STOKEN", "TEST-MID")

        self.assertEqual(result, {"ltoken": "TEST-ltoken", "cookie_token": "TEST-cookie_token"})
        self.assertEqual([str(request.url) for request in requests], [
            c.LTOKEN_BY_STOKEN_URL,
            c.COOKIE_TOKEN_BY_STOKEN_URL,
        ])
        for request in requests:
            with self.subTest(url=request.url):
                self.assertEqual(request.method, "GET")
                self.assertEqual(request.url.query, b"")
                self.assertEqual(request.headers["cookie"], "mid=TEST-MID;stoken=TEST-STOKEN")
                self.assertEqual(request.headers["x-rpc-app_id"], c.PASSPORT_APP_ID)
                self.assertEqual(request.headers["x-rpc-device_id"], "TEST-DEVICE")
                self.assertEqual(request.headers["x-rpc-device_fp"], "TEST-FP")
                self.assertEqual(request.headers["ds"], "TEST-PASSPORT-SIGNATURE")
                self.assertEqual(request.content, b"")

    def test_failed_exchange_reports_stage_and_does_not_continue(self) -> None:
        client = mock.Mock()
        client.get_json.return_value = {"retcode": 1002, "message": "系统请求失败，请返回重试"}
        login = PassportLogin(client, "TEST-DEVICE", "TEST-FP")
        with self.assertRaisesRegex(RuntimeError, "stoken 换 ltoken 失败: retcode=1002"):
            login.get_additional_tokens("TEST-STOKEN", "TEST-MID")
        client.get_json.assert_called_once()


if __name__ == "__main__":
    unittest.main()
