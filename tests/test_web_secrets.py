# -*- coding: utf-8 -*-
"""P4：/api/config 不能明文回传凭证，但保存配置又不能把凭证冲掉。

现状（web.py:254-256 + 1022-1024）：GET /api/config 原样深拷贝 self.config，
里面既有 web.password 的 sha256，也有账号 cookie/stoken/mid 和云游戏 token。
同文件 web.py:932-981 已经写了脱敏工具，却只用在日志上。

配套要求：前端是「取出再整体 POST 回去」的模型，
所以服务端必须把被掩码的字段还原，否则一保存就把凭证清空。
"""

from __future__ import annotations

import json
import unittest

from miyouqian.service.web import WebApp, hash_password
from tests.support import IsolatedConfigTest, base_config

SECRET_COOKIE = "COOKIE-SECRET-VALUE"
SECRET_STOKEN = "STOKEN-SECRET-VALUE"
SECRET_MID = "MID-SECRET-VALUE"
SECRET_CLOUD = "CLOUD-TOKEN-SECRET"
REAL_PASSWORD = "hunter2xy"


class ConfigSecretsTest(IsolatedConfigTest):
    def _app(self, *, with_password: bool = True) -> WebApp:
        config = base_config()
        if with_password:
            config["web"]["host"] = "0.0.0.0"  # 外网模式才需要密码
            config["web"]["password"] = hash_password(REAL_PASSWORD)
        config["accounts"] = [
            {
                "name": "A",
                "stuid": "104335735",
                "cookie": SECRET_COOKIE,
                "stoken": SECRET_STOKEN,
                "mid": SECRET_MID,
                "cloud_games": {"tokens": {"genshin": SECRET_CLOUD, "zzz": ""}},
            }
        ]
        self.write_config(config)
        return WebApp(self.config_path)

    # --- 不能泄漏 ---------------------------------------------------------
    def test_web_password_hash_is_not_returned(self) -> None:
        app = self._app()
        payload = app.get_config()
        self.assertFalse(
            payload["web"].get("password"),
            "/api/config 回传了 web.password（sha256 可离线爆破）",
        )

    def test_account_credentials_are_masked(self) -> None:
        app = self._app()
        blob = json.dumps(app.get_config(), ensure_ascii=False)
        for secret in (SECRET_COOKIE, SECRET_STOKEN, SECRET_MID, SECRET_CLOUD):
            self.assertNotIn(secret, blob, f"/api/config 明文回传了凭证 {secret!r}")

    def test_uid_is_still_visible_to_the_ui(self) -> None:
        """stuid 是界面要显示的账号标识，不能一起掩掉。"""
        app = self._app()
        self.assertEqual(app.get_config()["accounts"][0]["stuid"], "104335735")

    def test_masked_account_still_looks_logged_in(self) -> None:
        """前端用 account.cookie 的真值性判断账号是否已登录。"""
        app = self._app()
        account = app.get_config()["accounts"][0]
        self.assertTrue(account.get("cookie"), "掩码后 cookie 变空，前端会以为账号未登录")

    # --- 保存时不能冲掉 ---------------------------------------------------
    def test_saving_masked_payload_keeps_password(self) -> None:
        app = self._app()
        payload = app.get_config()
        payload["features"]["bbs_tasks"] = True
        app.set_config(payload)
        self.assertTrue(app.password_is_set, "保存配置把 web.password 冲空了")
        self.assertEqual(app.config["web"]["password"], hash_password(REAL_PASSWORD))

    def test_saving_masked_payload_keeps_credentials(self) -> None:
        app = self._app()
        payload = app.get_config()
        payload["features"]["bbs_tasks"] = True
        app.set_config(payload)
        account = app.config["accounts"][0]
        self.assertEqual(account["cookie"], SECRET_COOKIE, "保存配置把 cookie 冲掉了")
        self.assertEqual(account["stoken"], SECRET_STOKEN, "保存配置把 stoken 冲掉了")
        self.assertEqual(account["mid"], SECRET_MID, "保存配置把 mid 冲掉了")
        self.assertEqual(
            account["cloud_games"]["tokens"]["genshin"], SECRET_CLOUD,
            "保存配置把云游戏 token 冲掉了",
        )

    def test_credentials_survive_disk_round_trip(self) -> None:
        """掩码 → 保存 → 重新加载，凭证必须还在 data/credentials.yaml 里。"""
        app = self._app()
        payload = app.get_config()
        payload["features"]["bbs_tasks"] = True
        app.set_config(payload)

        reloaded = WebApp(self.config_path)
        account = reloaded.config["accounts"][0]
        self.assertEqual(account["cookie"], SECRET_COOKIE)
        self.assertEqual(account["stoken"], SECRET_STOKEN)

    def test_real_new_credential_overwrites_old_one(self) -> None:
        """用户真的重新扫码登录后，新的 cookie 必须能写进去。"""
        app = self._app()
        payload = app.get_config()
        payload["accounts"][0]["cookie"] = "BRAND-NEW-COOKIE"
        payload["accounts"][0]["stoken"] = "BRAND-NEW-STOKEN"
        app.set_config(payload)
        self.assertEqual(app.config["accounts"][0]["cookie"], "BRAND-NEW-COOKIE")
        self.assertEqual(app.config["accounts"][0]["stoken"], "BRAND-NEW-STOKEN")

    def test_emptying_credential_does_not_wipe_it(self) -> None:
        """掩码字段被清空时按「未修改」处理，避免误清。"""
        app = self._app()
        payload = app.get_config()
        payload["accounts"][0]["cookie"] = ""
        app.set_config(payload)
        self.assertEqual(app.config["accounts"][0]["cookie"], SECRET_COOKIE)

    def test_localhost_mode_password_survives_too(self) -> None:
        app = self._app(with_password=False)
        self.assertFalse(app.need_auth)
        payload = app.get_config()
        payload["features"]["bbs_tasks"] = True
        app.set_config(payload)
        self.assertEqual(app.config["accounts"][0]["cookie"], SECRET_COOKIE)


if __name__ == "__main__":
    unittest.main()
