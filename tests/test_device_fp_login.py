# -*- coding: utf-8 -*-
"""登录前必须用服务器签发的 device_fp；换证 Cookie 必须带 stuid。"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from miyouqian import constants as c
from miyouqian.auth.login import PassportLogin
from miyouqian.core import devicefp


class ServerFpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.device = {"id": "TEST-DEVICE-ID", "fp": "0123456789abc", "name": "Redmi Note 13 Pro", "model": "2312DRA50C"}

    def test_request_has_required_ext_fields(self) -> None:
        body = devicefp.build_fp_request(self.device)
        ext = json.loads(body["ext_fields"])
        self.assertEqual(body["platform"], "2")
        self.assertEqual(body["app_name"], "bbs_cn")
        self.assertEqual(body["bbs_device_id"], "TEST-DEVICE-ID")
        self.assertRegex(body["device_id"], r"^[0-9a-f]{16}$")
        self.assertTrue(ext["oaid"])
        self.assertTrue(ext["board"])
        self.assertEqual(ext["manufacturer"], "Xiaomi")

    def test_server_fp_is_saved_with_source(self) -> None:
        client = mock.Mock()
        client.post_json.return_value = {"retcode": 0, "data": {"code": 200, "device_fp": "38d7f2b1c0a9e"}}
        ok, reason = devicefp.ensure_server_fp(client, self.device)
        self.assertTrue(ok, reason)
        self.assertEqual(self.device["fp"], "38d7f2b1c0a9e")
        self.assertTrue(devicefp.has_server_fp(self.device))
        client.post_json.assert_called_once()
        self.assertEqual(client.post_json.call_args.args[0], c.DEVICE_FP_URL)

    def test_echoed_seed_with_code_500_is_not_success(self) -> None:
        client = mock.Mock()

        def echo(url, json):
            return {"retcode": 0, "data": {"code": 500, "msg": "内部错误", "device_fp": json["device_fp"]}}

        client.post_json.side_effect = echo
        with mock.patch.object(devicefp.time, "sleep"):
            ok, reason = devicefp.ensure_server_fp(client, self.device)
        self.assertFalse(ok)
        self.assertIn("内部错误", reason)
        self.assertEqual(self.device["fp"], "0123456789abc")
        self.assertFalse(devicefp.has_server_fp(self.device))

    def test_existing_server_fp_is_reused(self) -> None:
        self.device.update({"fp": "38d7f2b1c0a9e", "fp_source": "server"})
        client = mock.Mock()
        self.assertEqual(devicefp.ensure_server_fp(client, self.device), (True, ""))
        client.post_json.assert_not_called()


class ExchangeCookieTest(unittest.TestCase):
    def test_exchange_cookie_carries_stuid_stoken_mid(self) -> None:
        client = mock.Mock()
        client.get_json.side_effect = [
            {"retcode": 0, "data": {"ltoken": "L"}},
            {"retcode": 0, "data": {"cookie_token": "C"}},
        ]
        PassportLogin(client, "DEV", "FP").get_additional_tokens("v2_ST", "MID", "10001")
        for call in client.get_json.call_args_list:
            self.assertEqual(call.kwargs["headers"]["cookie"], "stuid=10001;stoken=v2_ST;mid=MID")


if __name__ == "__main__":
    unittest.main()
