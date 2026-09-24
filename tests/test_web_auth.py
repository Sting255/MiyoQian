# -*- coding: utf-8 -*-
"""P3：外网绑定必须要求认证。

`need_auth` 原来只看 config 里的 web.host，而 cli/`serve()` 把 --host 只用于 bind，
从不写回 config。于是 Dockerfile 的 `--host 0.0.0.0`（config.example.yaml 仍是
127.0.0.1）会让整个控制台在局域网上裸奔：读全部凭证、改配置、触发签到都不用密码。
"""

from __future__ import annotations

import unittest
from unittest import mock

from miyouqian.service import web as web_mod
from tests.support import IsolatedConfigTest, base_config


class _FakeServer:
    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        pass


class WebAuthBindingTest(IsolatedConfigTest):
    def _serve(self, config_host: str, bound_host: str):
        config = base_config()
        config["web"]["host"] = config_host
        self.write_config(config)
        with mock.patch.object(
            web_mod, "create_server", lambda host, port: (_FakeServer(), port)
        ):
            web_mod.serve(self.config_path, bound_host, 5890)
        return web_mod.Handler.app

    def test_binding_all_interfaces_requires_auth(self) -> None:
        """config 写 127.0.0.1，但实际绑 0.0.0.0（Docker 的启动方式）→ 必须认证。"""
        app = self._serve("127.0.0.1", "0.0.0.0")
        self.assertTrue(app.need_auth, "绑到 0.0.0.0 却不需要密码，等于把控制台公开")

    def test_localhost_config_and_binding_stays_passwordless(self) -> None:
        """纯本机使用保持原来的免密体验。"""
        app = self._serve("127.0.0.1", "127.0.0.1")
        self.assertFalse(app.need_auth)

    def test_external_config_still_requires_auth_on_localhost(self) -> None:
        """config 声明了外网，即使这次只绑回环也仍然要密码（宁严勿松）。"""
        app = self._serve("0.0.0.0", "127.0.0.1")
        self.assertTrue(app.need_auth)

    def test_lan_address_requires_auth(self) -> None:
        app = self._serve("192.168.1.10", "192.168.1.10")
        self.assertTrue(app.need_auth)


if __name__ == "__main__":
    unittest.main()
