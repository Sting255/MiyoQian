# -*- coding: utf-8 -*-
"""控制台运行时文件、免认证 /api/ping、单实例检查。

背景：`web.port` 被占时控制台会自动降级到别的端口，看门狗和用户脚本没法预先知道
实际端口，于是曾经出现「健康检查永远失败 → 又拉起一个实例 → 两个调度器并存」。
这里锁住三层：服务自己写实际端口、`/api/ping` 免认证探活、单实例检查一律保守。
"""

from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from miyouqian.core.config import runtime_path
from miyouqian.service import web as web_mod
from miyouqian.service.web import (
    APP_ID,
    WebApp,
    find_running_instance,
    hash_password,
    remove_runtime_file,
    write_runtime_file,
)
from tests.support import IsolatedConfigTest, base_config


class RuntimeFileTest(IsolatedConfigTest):
    def setUp(self) -> None:
        super().setUp()
        self.config = base_config()
        self.write_config(self.config)
        self.path = runtime_path(self.config_path, self.config)

    def test_write_creates_parent_dir_and_records_actual_port(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        info = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(info["app"], APP_ID)
        self.assertEqual(info["pid"], os.getpid())
        self.assertEqual(info["port"], 18990)
        self.assertEqual(info["url"], "http://127.0.0.1:18990")
        self.assertTrue(info["started_at"])
        self.assertEqual(info["config_path"], str(self.config_path.resolve()))

    def test_url_is_local_for_wildcard_host(self) -> None:
        write_runtime_file(self.path, "0.0.0.0", 18990, self.config_path)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["url"], "http://127.0.0.1:18990")

    def test_remove_only_deletes_own_file(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        remove_runtime_file(self.path)
        self.assertFalse(self.path.exists())

    def test_remove_keeps_file_written_by_another_process(self) -> None:
        # 停旧实例不能把新实例刚写的 runtime.json 删掉
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"app": APP_ID, "pid": os.getpid() + 1, "port": 18990}), encoding="utf-8")
        remove_runtime_file(self.path)
        self.assertTrue(self.path.exists())

    def test_remove_tolerates_missing_and_broken_file(self) -> None:
        remove_runtime_file(self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ 这不是 json", encoding="utf-8")
        remove_runtime_file(self.path)


class PingEndpointTest(IsolatedConfigTest):
    """免认证探活：外网模式下不带 Cookie 也要能拿到本应用标识。"""

    def setUp(self) -> None:
        super().setUp()
        config = base_config()
        config["web"] = {"host": "0.0.0.0", "port": 0, "password": hash_password("secret-pass")}
        self.write_config(config)
        self.app = WebApp(self.config_path, bound_host="0.0.0.0")
        self.assertTrue(self.app.need_auth)
        web_mod.Handler.app = self.app
        self.server, _ = web_mod.create_server("127.0.0.1", 0)
        self.port = int(self.server.server_address[1])
        self.app.bound_port = self.port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._shutdown)

    def _shutdown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def _get(self, path: str) -> tuple[int, dict]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}", timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8") or "{}")

    def test_ping_needs_no_login(self) -> None:
        status, body = self._get("/api/ping")
        self.assertEqual(status, 200)
        self.assertEqual(body["app"], APP_ID)
        self.assertEqual(body["pid"], os.getpid())
        self.assertEqual(body["port"], self.port)
        self.assertTrue(body["started_at"])

    def test_other_api_still_requires_login(self) -> None:
        status, _ = self._get("/api/status")
        self.assertEqual(status, 401)


class FindRunningInstanceTest(IsolatedConfigTest):
    def setUp(self) -> None:
        super().setUp()
        self.config = base_config()
        self.write_config(self.config)
        self.path = runtime_path(self.config_path, self.config)

    def _find(self):
        return find_running_instance(self.config_path, self.config)

    def test_no_runtime_file_means_start_normally(self) -> None:
        self.assertIsNone(self._find())

    def test_broken_file_means_start_normally(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ 坏掉了", encoding="utf-8")
        self.assertIsNone(self._find())

    def test_foreign_app_means_start_normally(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        info = json.loads(self.path.read_text(encoding="utf-8"))
        info["app"] = "something-else"
        self.path.write_text(json.dumps(info), encoding="utf-8")
        self.assertIsNone(self._find())

    def test_starting_instance_without_port_means_start_normally(self) -> None:
        # 刚写下 pid、还没绑上端口的那一段窗口
        write_runtime_file(self.path, "127.0.0.1", 0, self.config_path)
        self.assertIsNone(self._find())

    def test_unreachable_port_means_start_normally(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 1, self.config_path)
        with mock.patch.object(web_mod, "_http_get_json", lambda *a, **k: None):
            self.assertIsNone(self._find())

    def test_ping_answering_something_else_means_start_normally(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        with mock.patch.object(web_mod, "_http_get_json", lambda *a, **k: {"app": "nginx"}):
            self.assertIsNone(self._find())

    def test_ping_from_another_pid_means_start_normally(self) -> None:
        # 端口上是本应用，但不是文件里记的那个进程（旧实例已死、端口被别人接手）
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        with mock.patch.object(web_mod, "_http_get_json", lambda *a, **k: {"app": APP_ID, "pid": os.getpid() + 1}):
            self.assertIsNone(self._find())

    def test_live_instance_is_reported(self) -> None:
        write_runtime_file(self.path, "127.0.0.1", 18990, self.config_path)
        with mock.patch.object(web_mod, "_http_get_json", lambda *a, **k: {"app": APP_ID, "pid": os.getpid()}):
            found = self._find()
        self.assertIsNotNone(found)
        self.assertEqual(found["port"], 18990)
        self.assertEqual(found["url"], "http://127.0.0.1:18990")
        self.assertEqual(found["pid"], os.getpid())


if __name__ == "__main__":
    unittest.main()
