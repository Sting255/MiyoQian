# -*- coding: utf-8 -*-
"""P4：/api/config 不能明文回传凭证，但保存配置又不能把凭证冲掉。

现状（web.py:254-256 + 1022-1024）：GET /api/config 原样深拷贝 self.config，
里面既有 web.password 的 sha256，也有账号 cookie/stoken/mid 和云游戏 token。
同文件 web.py:932-981 已经写了脱敏工具，却只用在日志上。

配套要求：前端是「取出再整体 POST 回去」的模型，
所以服务端必须把被掩码的字段还原，否则一保存就把凭证清空。
"""

from __future__ import annotations

import copy
import json
import unittest

from miyouqian.service.web import WebApp, hash_password, public_config, restore_masked_secrets
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
                "stuid": "100000001",
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
        self.assertEqual(app.get_config()["accounts"][0]["stuid"], "100000001")

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

    def test_replacing_or_clearing_uid_cannot_inherit_old_credentials(self) -> None:
        for uid in ("200000002", ""):
            with self.subTest(uid=uid):
                app = self._app()
                payload = app.get_config()
                payload["accounts"][0]["stuid"] = uid
                app.set_config(payload)
                account = app.config["accounts"][0]
                for field in ("cookie", "stoken", "mid", "ltoken", "cookie_token"):
                    self.assertFalse(account.get(field), field)
                self.assertFalse(account["cloud_games"]["tokens"]["genshin"])

    def test_new_uid_with_explicit_cookie_does_not_mix_old_other_secrets(self) -> None:
        app = self._app()
        payload = app.get_config()
        payload["accounts"][0].update(stuid="200000002", cookie="NEW-ACCOUNT-COOKIE")
        app.set_config(payload)
        account = app.config["accounts"][0]
        self.assertEqual(account["cookie"], "NEW-ACCOUNT-COOKIE")
        self.assertFalse(account["stoken"])
        self.assertFalse(account["mid"])
        self.assertFalse(account["cloud_games"]["tokens"]["genshin"])

    def test_secret_restoration_requires_unique_uid_on_both_sides(self) -> None:
        original = {"accounts": [{"name": "A", "stuid": "101", "cookie": "fake-a"}]}
        for duplicate_old in (False, True):
            with self.subTest(duplicate_old=duplicate_old):
                old = copy.deepcopy(original)
                new = public_config(original)
                target = old if duplicate_old else new
                target["accounts"].append(copy.deepcopy(target["accounts"][0]))
                restore_masked_secrets(old, new)
                self.assertTrue(all(not a["cookie"] for a in new["accounts"]))

    def test_unique_uid_restoration_survives_reorder_and_whitespace(self) -> None:
        old = {"accounts": [{"name": "A", "stuid": " 101 ", "cookie": "fake-a"},
                            {"name": "B", "stuid": "202", "cookie": "fake-b"}]}
        new = public_config(old)
        new["accounts"].reverse()
        new["accounts"][1]["stuid"] = "101"
        restore_masked_secrets(old, new)
        self.assertEqual([a["cookie"] for a in new["accounts"]], ["fake-b", "fake-a"])

    def test_cloud_only_account_uses_unique_name_without_main_credentials(self) -> None:
        old = {"accounts": [{"name": "cloud", "stuid": "", "cookie": "unbound-cookie",
                             "cloud_games": {"tokens": {"genshin": "fake-cloud"}}}]}
        new = public_config(old)
        restore_masked_secrets(old, new)
        account = new["accounts"][0]
        self.assertFalse(account["cookie"])
        self.assertEqual(account["cloud_games"]["tokens"]["genshin"], "fake-cloud")

    def test_cloud_only_tokens_survive_save_and_reload(self) -> None:
        config = base_config()
        config["accounts"] = [{"name": "cloud", "stuid": "",
                               "cloud_games": {"tokens": {"genshin": "fake-cloud"}}}]
        self.write_config(config)
        app = WebApp(self.config_path)
        app.set_config(app.get_config())
        account = WebApp(self.config_path).config["accounts"][0]
        self.assertEqual(account["cloud_games"]["tokens"]["genshin"], "fake-cloud")
        self.assertFalse(account["cookie"])

    def test_cloud_name_fallback_rejects_ambiguous_or_new_nonempty_uid(self) -> None:
        original = {"accounts": [{"name": "cloud", "stuid": "",
                                  "cloud_games": {"tokens": {"genshin": "fake-cloud"}}}]}
        for action in ("duplicate_old", "duplicate_new", "new_uid"):
            with self.subTest(action=action):
                old, new = copy.deepcopy(original), public_config(original)
                if action == "new_uid":
                    new["accounts"][0]["stuid"] = "101"
                else:
                    target = old if action == "duplicate_old" else new
                    target["accounts"].append(copy.deepcopy(target["accounts"][0]))
                restore_masked_secrets(old, new)
                self.assertTrue(all(not a["cloud_games"]["tokens"]["genshin"] for a in new["accounts"]))


if __name__ == "__main__":
    unittest.main()
