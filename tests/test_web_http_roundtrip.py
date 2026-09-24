# -*- coding: utf-8 -*-
"""P4 的端到端验证：真的起一个 HTTP 服务，走完整的「取回 → 保存」往返。

单元测试只覆盖了 WebApp 的方法；这里要证明的是**线上真实路径**：
浏览器 GET /api/config 拿到的脱敏配置，原样 POST 回来后，
data/credentials.yaml 里的凭证和 web.password 必须一个不少。
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request

from miyouqian.core.config import load_config
from miyouqian.service import web as web_mod
from miyouqian.service.web import MASKED_SECRET, WebApp, hash_password
from tests.support import IsolatedConfigTest, base_config

COOKIE = "ltuid=1; ltoken=LT-LIVE-VALUE; cookie_token=CT-LIVE-VALUE"
STOKEN = "STOKEN-LIVE-VALUE"
MID = "MID-LIVE-VALUE"
PUSH_TOKEN = "PUSHPLUS-LIVE-TOKEN"
USERKEY = "DAMAGOU-LIVE-USERKEY"
PASSWORD = "e2e-pass-9527"


class HttpRoundTripTest(IsolatedConfigTest):
    def setUp(self) -> None:
        super().setUp()
        self.config = base_config()
        self.config["web"] = {"host": "127.0.0.1", "port": 0, "password": hash_password(PASSWORD)}
        self.config["accounts"] = [
            {
                "name": "A",
                "stuid": "104335735",
                "cookie": COOKIE,
                "stoken": STOKEN,
                "mid": MID,
                "cloud_games": {"tokens": {"genshin": "CLOUD-LIVE-TOKEN", "zzz": ""}},
            }
        ]
        self.config["push"]["channels"] = [
            {"provider": "pushplus", "enable": True, "token": PUSH_TOKEN, "topic": "t"}
        ]
        self.config["captcha"]["channels"] = [
            {
                "provider": "damagou",
                "enable": True,
                "userkey": USERKEY,
                "type": "",
                "timeout": 60,
            }
        ]
        self.write_config(self.config)

        self.app = WebApp(self.config_path, bound_host="127.0.0.1")
        web_mod.Handler.app = self.app
        self.server, _requested_port = web_mod.create_server("127.0.0.1", 0)
        # create_server 返回的是「尝试的端口」，绑 0 时要问 socket 实际拿到哪个
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, method: str, path: str, payload: dict | None = None, cookie: str = "") -> dict:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body,
            method=method,
            headers=headers,
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_full_round_trip_keeps_every_secret(self) -> None:
        fetched = self._request("GET", "/api/config")

        # 1) 响应里不能出现任何真值
        blob = json.dumps(fetched, ensure_ascii=False)
        for secret in (COOKIE, STOKEN, MID, PUSH_TOKEN, USERKEY, hash_password(PASSWORD)):
            self.assertNotIn(secret, blob, f"GET /api/config 泄漏了 {secret[:16]}...")
        self.assertNotIn("password", fetched["web"])
        self.assertEqual(fetched["accounts"][0]["cookie"], MASKED_SECRET)
        self.assertEqual(fetched["accounts"][0]["cloud_games"]["tokens"]["genshin"], MASKED_SECRET)
        self.assertEqual(fetched["push"]["channels"][0]["token"], MASKED_SECRET)
        self.assertEqual(fetched["captcha"]["channels"][0]["userkey"], MASKED_SECRET)
        # 界面要用的非敏感信息必须保留
        self.assertEqual(fetched["accounts"][0]["stuid"], "104335735")
        self.assertEqual(fetched["accounts"][0]["name"], "A")

        # 2) 浏览器把拿到的配置改一处再整体发回来
        fetched["features"]["bbs_tasks"] = True
        self.assertTrue(self._request("POST", "/api/config", fetched)["ok"])

        # 3) 磁盘上的凭证一个都不能少，也不能把掩码写进去
        reloaded = load_config(self.config_path)
        account = reloaded["accounts"][0]
        self.assertEqual(account["cookie"], COOKIE)
        self.assertEqual(account["stoken"], STOKEN)
        self.assertEqual(account["mid"], MID)
        self.assertEqual(account["cloud_games"]["tokens"]["genshin"], "CLOUD-LIVE-TOKEN")
        self.assertEqual(reloaded["push"]["channels"][0]["token"], PUSH_TOKEN)
        self.assertEqual(reloaded["captcha"]["channels"][0]["userkey"], USERKEY)
        self.assertEqual(reloaded["web"]["password"], hash_password(PASSWORD))
        self.assertTrue(reloaded["features"]["bbs_tasks"])
        self.assertNotIn(MASKED_SECRET, self.credentials_path.read_text(encoding="utf-8"))

    def test_second_save_is_stable(self) -> None:
        """连续保存两次不能出现「每存一次少一点」的漂移。"""
        first = self._request("GET", "/api/config")
        self._request("POST", "/api/config", first)
        second = self._request("GET", "/api/config")
        self._request("POST", "/api/config", second)

        reloaded = load_config(self.config_path)
        self.assertEqual(reloaded["accounts"][0]["cookie"], COOKIE)
        self.assertEqual(reloaded["push"]["channels"][0]["token"], PUSH_TOKEN)
        self.assertEqual(reloaded["web"]["password"], hash_password(PASSWORD))
        self.assertEqual(len(reloaded["accounts"]), 1)

    def test_new_login_cookie_is_accepted(self) -> None:
        """用户重新扫码后，新的真值必须能覆盖旧的。"""
        fetched = self._request("GET", "/api/config")
        fetched["accounts"][0]["cookie"] = "NEW-LIVE-COOKIE"
        fetched["accounts"][0]["stoken"] = "NEW-LIVE-STOKEN"
        self._request("POST", "/api/config", fetched)

        reloaded = load_config(self.config_path)
        self.assertEqual(reloaded["accounts"][0]["cookie"], "NEW-LIVE-COOKIE")
        self.assertEqual(reloaded["accounts"][0]["stoken"], "NEW-LIVE-STOKEN")

    def test_deleted_account_does_not_shift_credentials(self) -> None:
        """删掉第一个账号后，剩下那个不能继承被删账号的凭证。"""
        config = base_config()
        config["accounts"] = [
            {"name": "A", "stuid": "111", "cookie": "COOKIE-A", "stoken": "ST-A", "mid": "MID-A"},
            {"name": "B", "stuid": "222", "cookie": "COOKIE-B", "stoken": "ST-B", "mid": "MID-B"},
        ]
        self.write_config(config)
        self.app.config = load_config(self.config_path)

        fetched = self._request("GET", "/api/config")
        del fetched["accounts"][0]  # 只留 B，且 B 的凭证是掩码
        self._request("POST", "/api/config", fetched)

        reloaded = load_config(self.config_path)
        self.assertEqual(len(reloaded["accounts"]), 1)
        self.assertEqual(reloaded["accounts"][0]["name"], "B")
        self.assertEqual(reloaded["accounts"][0]["cookie"], "COOKIE-B", "删号后凭证串号了")
        self.assertEqual(reloaded["accounts"][0]["stoken"], "ST-B")

    def test_auth_is_enforced_over_http_when_bound_externally(self) -> None:
        """绑到 0.0.0.0 时 /api/config 必须要求登录（这里用 App 的判定复现）。"""
        self.app.bound_host = "0.0.0.0"
        self.assertTrue(self.app.need_auth)
        try:
            self._request("GET", "/api/config")
            self.fail("未登录却拿到了 /api/config")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)

    def test_first_run_password_setup_flow(self) -> None:
        """Docker 场景：绑 0.0.0.0、配置里还是 127.0.0.1、没设过密码。

        开启认证之后必须仍有一条自助设置密码的路，否则用户会被锁在门外。
        """
        config = base_config()
        config["web"] = {"host": "127.0.0.1", "port": 0, "password": ""}  # 注意：密码为空
        self.write_config(config)
        app = WebApp(self.config_path, bound_host="0.0.0.0")
        self.assertTrue(app.need_auth, "绑 0.0.0.0 却没要求认证")
        self.assertFalse(app.password_is_set)
        web_mod.Handler.app = app
        self.addCleanup(lambda: setattr(web_mod.Handler.app, "bound_host", "127.0.0.1"))
        self.app.bound_host = "0.0.0.0"

        status = self._request("GET", "/api/auth/status")
        self.assertTrue(status["need_auth"])
        self.assertFalse(status["password_set"])

        try:
            self._request("GET", "/api/config")
            self.fail("未登录就拿到了配置")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 401)

        # 首次访问：用 /api/auth/setup 自助设置密码，拿到会话后即可正常使用
        token = self._setup_password("first-run-pass")
        self.assertTrue(token)
        fetched = self._request("GET", "/api/config", cookie=token)
        self.assertIn("features", fetched)
        self.assertEqual(fetched["accounts"], [])
        self.assertTrue(app.password_is_set, "设置的密码没有被保存")
        self.assertTrue(app._check_session(token.split("=", 1)[-1]))

    def _setup_password(self, password: str) -> str:
        body = json.dumps({"password": password}).encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/auth/setup",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.assertEqual(response.status, 200)
            cookie = response.headers.get("Set-Cookie") or ""
        return cookie.split(";", 1)[0]  # myq_token=...


if __name__ == "__main__":
    unittest.main()
