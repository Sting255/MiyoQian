# -*- coding: utf-8 -*-
"""「验证码环境自检」：不等 9 点签到失败才发现环境问题。

这里不真启动 Chrome（单测环境里没有浏览器可用），而是锁住三件事：
1. WebApp.check_captcha_env 会把 nine.self_check 的结果原样带回，
   并把通过/失败写进日志（用户在网页日志里能看到）；
2. POST /api/captcha/check 路由真的存在且要求登录（用 http.client 只连
   本测试自己起的 127.0.0.1 服务，不发任何外部请求）；
3. 网页上有按钮、有结果容器，js 里有对应处理函数。
真实拉起 Chrome 的验证由用户在服务环境里点按钮完成。
"""

from __future__ import annotations

import http.client
import json
import pathlib
import secrets
import threading
import unittest
from unittest import mock

from tests.support import IsolatedConfigTest, base_config

from miyouqian.service import web as web_mod
from miyouqian.service.web import WebApp, hash_password

# 单测专用的一次性口令：每次运行随机生成，只喂给本地回环服务，不是任何环境的真实凭据
PASSWORD = secrets.token_urlsafe(12)
ROOT = pathlib.Path(__file__).resolve().parents[1]


class CaptchaEnvCheckTest(IsolatedConfigTest):
    def setUp(self) -> None:
        super().setUp()
        config = base_config()
        config["web"] = {"host": "127.0.0.1", "port": 0, "password": hash_password(PASSWORD)}
        config["captcha"] = {
            "enable": True,
            "max_retries": 3,
            "channels": [{"provider": "local", "enable": True, "headless": True}],
        }
        self.write_config(config)

        self.app = WebApp(self.config_path, bound_host="0.0.0.0")
        web_mod.Handler.app = self.app
        self.server, _port = web_mod.create_server("127.0.0.1", 0)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _request(self, method: str, path: str, payload: dict | None = None, cookie: str = ""):
        """只连本测试在 127.0.0.1 上起的服务。返回 (状态码, 响应体, Set-Cookie)。"""
        body = None if payload is None else json.dumps(payload)
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            data = json.loads(response.read().decode("utf-8"))
            set_cookie = response.getheader("Set-Cookie") or ""
            return response.status, data, set_cookie
        finally:
            conn.close()

    def _login(self) -> str:
        status, body, set_cookie = self._request("POST", "/api/auth/login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        # token 只放在 HttpOnly Cookie 里，从 Set-Cookie 头取
        for part in set_cookie.split(";"):
            part = part.strip()
            if part.startswith("myq_token="):
                return part[len("myq_token="):]
        self.fail(f"登录响应没有下发 token: {set_cookie!r}")

    def test_success_is_passed_through_and_logged(self) -> None:
        fake = {"ok": True, "browser": "chrome.exe", "seconds": 2.1}
        with mock.patch.object(web_mod, "captcha_self_check", return_value=fake):
            result = self.app.check_captcha_env()
        self.assertEqual(result, fake)
        logs = " ".join(str(line) for line in self.app.logs)
        self.assertIn("自检通过", logs)

    def test_failure_logs_the_reason(self) -> None:
        fake = {"ok": False, "browser": "", "error": "未找到可用的 Chrome / Edge 浏览器"}
        with mock.patch.object(web_mod, "captcha_self_check", return_value=fake):
            result = self.app.check_captcha_env()
        self.assertEqual(result, fake)
        logs = " ".join(str(line) for line in self.app.logs)
        self.assertIn("自检失败", logs)
        self.assertIn("未找到可用的 Chrome", logs)

    def test_route_requires_auth(self) -> None:
        status, _body, _cookie = self._request("POST", "/api/captcha/check", {})
        self.assertEqual(status, 401)

    def test_route_returns_self_check_payload(self) -> None:
        cookie = self._login()
        fake = {"ok": True, "browser": "chrome.exe", "seconds": 1.5}
        with mock.patch.object(web_mod, "captcha_self_check", return_value=fake):
            status, body, _cookie = self._request(
                "POST", "/api/captcha/check", {}, cookie=f"myq_token={cookie}"
            )
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertEqual(body["browser"], "chrome.exe")

    def test_headless_follows_local_channel_config(self) -> None:
        captured: dict = {}

        def fake_self_check(headless: bool = True) -> dict:
            captured["headless"] = headless
            return {"ok": True, "browser": "x", "seconds": 0.1}

        with mock.patch.object(web_mod, "captcha_self_check", fake_self_check):
            self.app.check_captcha_env()
        self.assertTrue(captured["headless"])

        with self.app.lock:
            self.app.config["captcha"]["channels"][0]["headless"] = False
        with mock.patch.object(web_mod, "captcha_self_check", fake_self_check):
            self.app.check_captcha_env()
        self.assertFalse(captured["headless"])


class CaptchaCheckFrontendTest(unittest.TestCase):
    """前端：按钮、结果容器、处理函数、API 地址四件套要都在。"""

    def test_html_has_button_and_result_node(self) -> None:
        html = (ROOT / "miyouqian" / "webui" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="captchaCheckBtn"', html)
        self.assertIn('id="captchaCheckResult"', html)

    def test_js_binds_button_and_calls_route(self) -> None:
        js = (ROOT / "miyouqian" / "webui" / "app.js").read_text(encoding="utf-8")
        self.assertIn("checkCaptchaEnv", js)
        self.assertIn("/api/captcha/check", js)
        self.assertIn('$("captchaCheckBtn")', js)


if __name__ == "__main__":
    unittest.main()
