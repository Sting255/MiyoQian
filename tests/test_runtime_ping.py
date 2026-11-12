# -*- coding: utf-8 -*-
"""控制台运行时文件、免认证 /api/ping、单实例检查。

背景：`web.port` 被占时控制台会自动降级到别的端口，看门狗和用户脚本没法预先知道
实际端口，于是曾经出现「健康检查永远失败 → 又拉起一个实例 → 两个调度器并存」。
这里锁住三层：服务自己写实际端口、`/api/ping` 免认证探活、单实例检查一律保守。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import subprocess
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock

from miyouqian import cli
from miyouqian.core.config import load_config, runtime_path
from miyouqian.service import web as web_mod
from miyouqian.service.web import (
    APP_ID,
    WebApp,
    find_running_instance,
    hash_password,
    remove_runtime_file,
    write_runtime_file,
)
from tests.support import ROOT, IsolatedConfigTest, base_config


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

    def test_ping_hides_pid_from_non_loopback_clients(self) -> None:
        # 外网模式下匿名请求只该知道「是不是本应用」
        body = self.app.ping("192.168.1.9")
        self.assertEqual(set(body), {"app", "port"})
        self.assertEqual(self.app.ping("127.0.0.1")["pid"], os.getpid())
        self.assertEqual(self.app.ping("::1")["pid"], os.getpid())

    def test_ipv4_mapped_loopback_counts_as_local(self) -> None:
        # 双栈监听时来源会显示成 ::ffff:127.0.0.1，字符串白名单会漏掉 -> 单实例检查失效
        self.assertEqual(self.app.ping("::ffff:127.0.0.1")["pid"], os.getpid())
        self.assertEqual(self.app.ping("::ffff:192.168.1.9"), {"app": APP_ID, "port": self.port})
        self.assertNotIn("pid", self.app.ping("fe80::1%eth0"))

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


class EndToEndTest(IsolatedConfigTest):
    """真实链路：写 runtime.json → 真服务器回 ping → find_running_instance。

    这条链路以前零覆盖，两个 ps1 的 P2 就是从这里漏出去的，所以这里一律不 mock。
    """

    def setUp(self) -> None:
        super().setUp()
        self.config = base_config()
        self.config["web"] = {"host": "127.0.0.1", "port": 0, "password": ""}
        self.write_config(self.config)
        self.app = WebApp(self.config_path, bound_host="127.0.0.1")
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

    def test_runtime_file_ping_and_detection_agree(self) -> None:
        path = runtime_path(self.config_path, self.config)
        write_runtime_file(path, "127.0.0.1", self.port, self.config_path)

        found = find_running_instance(self.config_path, load_config(self.config_path))

        self.assertIsNotNone(found)
        self.assertEqual(found["pid"], os.getpid())
        self.assertEqual(found["port"], self.port)
        self.assertEqual(found["url"], f"http://127.0.0.1:{self.port}")

    def test_another_config_sharing_the_data_dir_is_not_blocked(self) -> None:
        # 同目录另一份 config 共享 data/runtime.json：那是另一份副本，不该被「已有实例」挡回
        path = runtime_path(self.config_path, self.config)
        write_runtime_file(path, "127.0.0.1", self.port, self.config_path)
        other = self.dir / "config-test.yaml"
        other.write_text(self.config_path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertIsNotNone(find_running_instance(self.config_path, load_config(self.config_path)))
        self.assertIsNone(find_running_instance(other, load_config(self.config_path)))

    def test_detection_fails_when_the_port_belongs_to_another_app(self) -> None:
        # 服务已死、端口被别的程序占住：不能当成「还活着」
        path = runtime_path(self.config_path, self.config)
        write_runtime_file(path, "127.0.0.1", 1, self.config_path)  # 1 号端口不会是本应用
        self.assertIsNone(find_running_instance(self.config_path, load_config(self.config_path)))


class ServeLifecycleTest(IsolatedConfigTest):
    """serve() 两阶段写 runtime.json，停止时删掉自己那份。"""

    def setUp(self) -> None:
        super().setUp()
        self.config = base_config()
        self.config["web"] = {"host": "127.0.0.1", "port": 0, "password": ""}
        self.write_config(self.config)
        self.path = runtime_path(self.config_path, self.config)

    def test_port_is_recorded_after_bind_and_file_removed_on_exit(self) -> None:
        during: dict = {}

        def fake_serve_forever(_self):
            during.update(json.loads(self.path.read_text(encoding="utf-8")))
            raise KeyboardInterrupt

        with mock.patch.object(web_mod.ThreadingHTTPServer, "serve_forever", fake_serve_forever):
            web_mod.serve(self.config_path, "127.0.0.1", 0)

        self.assertEqual(during["app"], APP_ID)
        self.assertEqual(during["pid"], os.getpid())
        self.assertGreater(during["port"], 0, "绑定成功后必须写真实端口，而不是占位的 0")
        self.assertFalse(self.path.exists(), "停止后要删掉自己写的 runtime.json")

    def test_placeholder_is_removed_when_binding_fails(self) -> None:
        with mock.patch.object(web_mod, "create_server", side_effect=OSError("端口全被占了")):
            with self.assertRaises(OSError):
                web_mod.serve(self.config_path, "127.0.0.1", 18990)
        self.assertFalse(self.path.exists(), "起不来时不能留下占位文件骗看门狗")


class CommandServeTest(IsolatedConfigTest):
    """单实例：互斥体被占、或探测到已有实例，都让位（返回 0，不起服务）。"""

    def setUp(self) -> None:
        super().setUp()
        self.write_config(base_config())

    def _run(self, mutex, found):
        out = io.StringIO()
        with (
            mock.patch.object(cli, "_acquire_single_instance", lambda *_a, **_k: mutex),
            mock.patch("miyouqian.service.web.find_running_instance", return_value=found),
            mock.patch("miyouqian.service.web.serve") as serve_mock,
            contextlib.redirect_stdout(out),
        ):
            code = cli.command_serve(self.config_path, None, None)
        return code, out.getvalue(), serve_mock

    def test_defers_when_mutex_is_busy(self) -> None:
        code, output, serve_mock = self._run(None, None)
        self.assertEqual(code, 0)
        self.assertIn("已有实例正在运行", output)
        serve_mock.assert_not_called()

    def test_defers_when_a_live_instance_is_found(self) -> None:
        found = {"pid": 4242, "port": 18990, "url": "http://127.0.0.1:18990"}
        code, output, serve_mock = self._run(object(), found)
        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:18990", output)
        self.assertIn("4242", output)
        serve_mock.assert_not_called()

    def test_starts_when_nothing_is_running(self) -> None:
        code, _output, serve_mock = self._run(object(), None)
        self.assertEqual(code, 0)
        serve_mock.assert_called_once()


class SingleInstanceLockTest(IsolatedConfigTest):
    def test_second_lock_from_the_same_config_is_refused(self) -> None:
        self.write_config(base_config())
        first = cli._acquire_single_instance(self.config_path)
        second = cli._acquire_single_instance(self.config_path)
        try:
            self.assertIsNotNone(first, "第一次取锁不该失败")
            self.assertIsNone(second, "同一份配置的第二把锁必须被拒")
        finally:
            self._release(first)

    def test_other_config_is_not_blocked_by_this_one(self) -> None:
        # 另一份 config/data 起第二份副本是合法用法：锁名必须跟着配置路径走。
        # 跨进程验证（同进程里两个不同名字的互斥体本来就不冲突，测不出问题）。
        self.write_config(base_config())
        other = self.dir / "other.yaml"
        other.write_text(self.config_path.read_text(encoding="utf-8"), encoding="utf-8")
        held = cli._acquire_single_instance(self.config_path)
        self.assertIsNotNone(held)
        self.addCleanup(self._release, held)

        def probe(path: pathlib.Path) -> str:
            code = (
                "import sys; from miyouqian.cli import _acquire_single_instance;"
                "print('free' if _acquire_single_instance(sys.argv[1]) is not None else 'busy')"
            )
            done = subprocess.run(
                [sys.executable, "-c", code, str(path)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=120,
            )
            return done.stdout.strip()

        self.assertEqual(probe(self.config_path), "busy", "同一份配置必须被挡住")
        self.assertEqual(probe(other), "free", "另一份配置不该被误挡")

    @staticmethod
    def _release(handle: object) -> None:
        if not handle:
            return
        import ctypes

        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(handle))


if __name__ == "__main__":
    unittest.main()
