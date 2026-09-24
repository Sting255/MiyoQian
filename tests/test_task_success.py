# -*- coding: utf-8 -*-
"""P7：cookie/stoken 失效必须报成失败，不能被当成「任务完成」。

games.py 把 `retcode != 0`（含 -100/-101 登录失效）和「确实没绑定角色」混成一类，
只输出「未找到绑定角色」并计入 skipped，汇总成「成功 0，失败 0，跳过 1」。
`notifier.is_task_success` 只看失败计数，于是推送显示「任务完成」——
cookie 过期是高频场景，用户收不到任何失败信号。
"""

from __future__ import annotations

import unittest
from unittest import mock

from miyouqian import constants as c
from miyouqian.service.notifier import is_task_success
from miyouqian.tasks import games as games_mod
from miyouqian.tasks.games import GameCheckin
from tests.support import base_config

LOGIN_FAILED = {"retcode": -100, "message": "登录失效，请重新登录", "data": None}
NO_ROLE = {"retcode": 0, "message": "OK", "data": {"list": []}}
OK_EMPTY = {"retcode": 0, "message": "OK", "data": {}}


class RolesClient:
    def __init__(self, roles_response):
        self.roles_response = roles_response

    def get_json(self, url, **kwargs):
        if url == c.ACCOUNT_ROLES_URL:
            return self.roles_response
        return OK_EMPTY

    def post_json(self, url, **kwargs):
        return OK_EMPTY


def run_once(response) -> list[str]:
    config = base_config()
    config["games"]["enabled"] = ["genshin"]
    client = RolesClient(response)
    with mock.patch.object(games_mod, "refresh_cookie_token", lambda *a, **k: False):
        return GameCheckin(client, config, {"name": "A", "cookie": "stub"}).run()


class ExpiredLoginTest(unittest.TestCase):
    def test_expired_cookie_is_a_failure(self) -> None:
        lines = run_once(LOGIN_FAILED)
        self.assertIn("失败 1", "\n".join(lines), f"cookie 失效没有计入失败：{lines}")
        self.assertFalse(is_task_success(lines), "cookie 失效被判成了任务成功")

    def test_expired_cookie_message_mentions_login(self) -> None:
        lines = run_once(LOGIN_FAILED)
        joined = "\n".join(lines)
        self.assertTrue(
            "登录" in joined or "cookie" in joined.lower(),
            f"失败原因没有提示是登录失效：{lines}",
        )

    def test_no_role_binding_is_still_just_skipped(self) -> None:
        """真的没绑角色时不应升级成失败，否则会天天误报。"""
        lines = run_once(NO_ROLE)
        self.assertIn("跳过 1", "\n".join(lines), lines)

    def test_successful_day_still_reports_success(self) -> None:
        config = base_config()
        config["games"]["enabled"] = ["genshin"]
        roles = {"retcode": 0, "data": {"list": [{"game_uid": "9", "nickname": "N", "region": "cn_gf01"}]}}

        class SignedClient:
            def get_json(self, url, **kwargs):
                if url == c.ACCOUNT_ROLES_URL:
                    return roles
                return {"retcode": 0, "data": {"awards": [{"name": "矿", "cnt": 3}]}}

            def post_json(self, url, **kwargs):
                if "info" in url:
                    return {"retcode": 0, "data": {"is_sign": True, "total_sign_day": 1}}
                return {"retcode": 0, "data": {"success": 0}}

        with mock.patch.object(games_mod, "refresh_cookie_token", lambda *a, **k: False):
            lines = GameCheckin(SignedClient(), config, {"name": "A", "cookie": "x"}).run()
        self.assertTrue(is_task_success(lines), lines)


if __name__ == "__main__":
    unittest.main()
