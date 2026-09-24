# -*- coding: utf-8 -*-
"""验证码模块的健壮性：不涉及识别判定，只修「起不来 / 泄漏 / 没法排查」。

明确不动的东西（改这些会掉成功率，项目记忆里有教训）：
`MIN_SIMILARITY` / `GAP_THRESHOLD` / `MAX_TILES` / `GRID_EDGE` / `ICON_BOX` /
`pick()` / `split_composite()` / `trim()` / `preprocess()` / `PANEL_JS`。

这些测试全部不启动真实 Chrome（本沙箱禁命名管道，Chrome 的 Mojo IPC 起不来），
所以测的是「启动失败时行为对不对」，而不是「Chrome 能不能跑」——
后者由网页上的「环境自检」在服务环境里回答。
"""

from __future__ import annotations

import pathlib
import socket
import tempfile
import unittest
import urllib.error
from unittest import mock

import websocket

from miyouqian.core.geetest import browser as gb
from miyouqian.core.geetest import nine
from tests.support import make_case_dir


def make_browser(port: int = 45678, proc: mock.Mock | None = None) -> gb.Browser:
    """不启动 Chrome，只构造一个够用的 Browser 对象。"""
    instance = gb.Browser.__new__(gb.Browser)
    instance.port = port
    instance.proc = proc
    instance._log = None
    instance.log_path = pathlib.Path(tempfile.gettempdir()) / "myq-not-a-real-log.log"
    instance.profile = None
    instance.server = None
    instance.page = None
    instance.headless = True
    return instance


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.status = 200

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class TargetWebsocketRetryTest(unittest.TestCase):
    """`_target_ws` 的重试必须真的生效（原来 urlopen 没包 try，一次瞬时错误就抛出）。"""

    def test_retries_transient_error_then_succeeds(self) -> None:
        instance = make_browser()
        calls = {"n": 0}

        def fake_urlopen(url: str, timeout: float = 3):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError("connection refused")
            return _FakeResponse(
                b'[{"type":"page","webSocketDebuggerUrl":"ws://127.0.0.1:1/devtools/page/X"}]'
            )

        with mock.patch.object(gb.urllib.request, "urlopen", fake_urlopen), \
             mock.patch.object(gb.time, "sleep", lambda _s: None):
            result = instance._target_ws(timeout=5.0)

        self.assertEqual(result, "ws://127.0.0.1:1/devtools/page/X")
        self.assertGreaterEqual(calls["n"], 2, "第一次失败后没有重试")

    def test_raises_early_when_browser_process_already_exited(self) -> None:
        proc = mock.Mock()
        proc.poll.return_value = 21  # 进程已退出
        proc.returncode = 21
        instance = make_browser(proc=proc)

        with mock.patch.object(
            gb.urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")
        ), mock.patch.object(gb.time, "sleep", lambda _s: None):
            with self.assertRaises(RuntimeError) as ctx:
                instance._target_ws(timeout=5.0)

        self.assertIn("退出", str(ctx.exception))

    def test_timeout_message_includes_last_error(self) -> None:
        instance = make_browser()
        with mock.patch.object(
            gb.urllib.request, "urlopen", side_effect=urllib.error.URLError("boom")
        ), mock.patch.object(gb.time, "sleep", lambda _s: None), \
             mock.patch.object(gb.time, "time", side_effect=[0, 0, 99]):
            with self.assertRaises(TimeoutError) as ctx:
                instance._target_ws(timeout=1.0)
        self.assertIn("boom", str(ctx.exception))


class PopenFlagsTest(unittest.TestCase):
    """POSIX 上传 creationflags 会直接抛 ValueError，必须按平台给。"""

    def test_posix_gets_no_creationflags(self) -> None:
        self.assertEqual(gb.Browser._popen_kwargs("posix"), {})

    def test_windows_gets_new_process_group(self) -> None:
        self.assertEqual(gb.Browser._popen_kwargs("nt"), {"creationflags": 0x00000200})

    def test_start_once_passes_platform_kwargs(self) -> None:
        instance = make_browser()
        instance._wait_devtools = lambda: None          # type: ignore[assignment]
        instance._target_ws = lambda: "ws://x"          # type: ignore[assignment]
        instance._tail = lambda limit=400: ""           # type: ignore[assignment]
        instance._log = mock.Mock()
        page = mock.Mock()
        with mock.patch.object(gb, "find_browser", lambda: "chrome"), \
             mock.patch.object(gb, "CdpPage", lambda url, **kw: page), \
             mock.patch.object(gb.subprocess, "Popen") as popen, \
             mock.patch.object(
                 gb.Browser, "_popen_kwargs", staticmethod(lambda platform=None: {})
             ):
            instance._start_once()

        self.assertNotIn("creationflags", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.args[0][0], "chrome")


class BrowserConstructionTest(unittest.TestCase):
    """构造函数中途失败必须把已经占用的资源还回去。"""

    def test_profile_is_removed_when_harness_fails(self) -> None:
        created: list[pathlib.Path] = []

        def fake_mkdtemp(prefix: str = "", suffix: str = "", dir: str | None = None) -> str:
            path = pathlib.Path(tempfile.gettempdir()) / f"{prefix}unittest-{len(created)}"
            path.mkdir(parents=True, exist_ok=True)
            created.append(path)
            return str(path)

        with mock.patch.object(gb.tempfile, "mkdtemp", fake_mkdtemp), \
             mock.patch.object(gb, "HarnessServer", side_effect=OSError("port taken")):
            with self.assertRaises(OSError):
                gb.Browser()

        self.assertTrue(created, "用例本身没有走到建 profile 这一步")
        for path in created:
            self.assertFalse(path.exists(), f"profile 残留: {path}")

    def test_close_is_safe_for_a_half_built_instance(self) -> None:
        instance = make_browser()
        instance.close()  # 不应抛异常
        self.assertIsNone(instance.page)

    def test_tail_tolerates_missing_log_file(self) -> None:
        instance = make_browser()
        self.assertEqual(instance._tail(), "无输出")


class KillLoggingTest(unittest.TestCase):
    """CDP 优雅关闭失败时会静默回退强杀 —— 必须留下痕迹。"""

    def test_warns_when_falling_back_to_force_kill(self) -> None:
        instance = make_browser()
        fake_logger = mock.Mock()
        with mock.patch.object(gb, "logger", fake_logger), \
             mock.patch.object(instance, "_close_via_cdp", lambda: False), \
             mock.patch.object(instance, "_force_kill", lambda: None):
            instance._kill()

        fake_logger.bind.assert_called()
        warned = " ".join(
            str(call.args[0]) for call in fake_logger.bind.return_value.warning.call_args_list
        )
        self.assertIn("强杀", warned)

    def test_no_warning_when_cdp_close_works(self) -> None:
        instance = make_browser()
        fake_logger = mock.Mock()
        with mock.patch.object(gb, "logger", fake_logger), \
             mock.patch.object(instance, "_close_via_cdp", lambda: True), \
             mock.patch.object(instance, "_wait_exit", lambda timeout=8.0: True), \
             mock.patch.object(instance, "_force_kill", lambda: None):
            instance._kill()

        fake_logger.bind.assert_not_called()


class HarnessServerRetryTest(unittest.TestCase):
    """free_port 是先 bind 再 close，存在被抢占的窗口；harness 要能换端口重试。"""

    def test_retries_when_port_is_taken(self) -> None:
        import http.server

        real_server = http.server.ThreadingHTTPServer
        attempts = {"n": 0}
        holder: list = []

        def flaky(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("port in use")
            server = real_server(*args, **kwargs)
            holder.append(server)
            return server

        with mock.patch.object(http.server, "ThreadingHTTPServer", flaky):
            server = gb.HarnessServer(gb.HARNESS.parent, attempts=3)
        self.addCleanup(server.close)
        self.assertGreaterEqual(attempts["n"], 2, "第一次失败后没有换端口重试")

    def test_raises_after_exhausting_attempts(self) -> None:
        import http.server

        with mock.patch.object(
            http.server, "ThreadingHTTPServer", side_effect=OSError("nope")
        ):
            with self.assertRaises(OSError):
                gb.HarnessServer(gb.HARNESS.parent, attempts=2)


class WaitForConnectionTest(unittest.TestCase):
    """连接层错误不能被当成「页面还没准备好」。"""

    def _page(self, behaviour) -> gb.CdpPage:
        page = gb.CdpPage.__new__(gb.CdpPage)
        page.evaluate = behaviour  # type: ignore[assignment]
        return page

    def test_connection_error_propagates_immediately(self) -> None:
        def boom(_expression: str):
            raise websocket.WebSocketConnectionClosedException("closed")

        page = self._page(boom)
        started = __import__("time").time()
        with self.assertRaises(RuntimeError) as ctx:
            page.wait_for("1 + 1", timeout=10.0)
        self.assertIn("CDP", str(ctx.exception))
        self.assertLess(__import__("time").time() - started, 2.0, "没有立即抛出")

    def test_page_error_is_still_treated_as_not_ready(self) -> None:
        calls = {"n": 0}

        def flaky(_expression: str):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("页面脚本异常")
            return True

        page = self._page(flaky)
        with mock.patch.object(gb.time, "sleep", lambda _s: None):
            self.assertTrue(page.wait_for("1 + 1", timeout=5.0))


class ModelDownloadTest(unittest.TestCase):
    """模型下载：不能留半成品，也不能无上限地拉。"""

    def setUp(self) -> None:
        # 不要用 tempfile.mkdtemp：它建在 %TEMP% 下，本沙箱会拒绝在那里建目录/文件
        # （support.make_case_dir 已经把目录放到仓库内的 .tmp-tests/ 里）
        self.dir = make_case_dir()
        self.addCleanup(__import__("shutil").rmtree, self.dir, True)
        self.target = self.dir / nine.MODEL_FILENAME

    def test_partial_file_is_removed_on_failure(self) -> None:
        class BoomResponse:
            def __init__(self) -> None:
                self.reads = 0

            def read(self, _size: int = 0) -> bytes:
                self.reads += 1
                if self.reads == 1:
                    return b"x" * 1024
                raise OSError("connection reset")

            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        with mock.patch.object(gb.urllib.request, "urlopen", lambda *a, **k: BoomResponse()):
            with self.assertRaises(OSError):
                nine.ensure_model(self.target)

        self.assertFalse(self.target.with_suffix(self.target.suffix + ".part").exists(),
                         "半成品 .part 没有被清理")
        self.assertFalse(self.target.exists())

    def test_oversized_download_is_aborted(self) -> None:
        chunk = b"x" * (1024 * 512)
        total_chunks = nine.MODEL_MAX_BYTES // len(chunk) + 5

        class HugeResponse:
            def __init__(self) -> None:
                self.n = 0

            def read(self, _size: int = 0) -> bytes:
                self.n += 1
                if self.n > total_chunks:
                    return b""
                return chunk

            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        with mock.patch.object(gb.urllib.request, "urlopen", lambda *a, **k: HugeResponse()):
            with self.assertRaises(nine.ModelUnavailable):
                nine.ensure_model(self.target)

        self.assertFalse(self.target.exists())
        self.assertFalse(self.target.with_suffix(self.target.suffix + ".part").exists())

    def test_timeout_is_enforced(self) -> None:
        class SlowResponse:
            def read(self, _size: int = 0) -> bytes:
                return b"x" * 1024

            def __enter__(self):
                return self

            def __exit__(self, *exc: object) -> bool:
                return False

        with mock.patch.object(gb.urllib.request, "urlopen", lambda *a, **k: SlowResponse()), \
             mock.patch.object(nine, "MODEL_DOWNLOAD_TIMEOUT", 0), \
             mock.patch.object(nine.time, "time", lambda: 10_000.0):
            with self.assertRaises(nine.ModelUnavailable):
                nine.ensure_model(self.target)

        self.assertFalse(self.target.with_suffix(self.target.suffix + ".part").exists())

    def test_existing_model_is_reused(self) -> None:
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_bytes(b"x" * (nine.MODEL_MIN_BYTES + 1))
        with mock.patch.object(gb.urllib.request, "urlopen", side_effect=AssertionError("不该下载")):
            self.assertEqual(nine.ensure_model(self.target), self.target)


class ModelPathTest(unittest.TestCase):
    """模型路径不能跟着当前工作目录跑（换个 CWD 就重下 85MB）。"""

    def test_default_path_is_repo_relative(self) -> None:
        repo_root = pathlib.Path(nine.__file__).resolve().parents[3]
        expected = repo_root / "data" / "models" / nine.MODEL_FILENAME
        self.assertEqual(nine.model_path(), expected)

    def test_default_path_does_not_depend_on_cwd(self) -> None:
        before = nine.model_path()
        original = pathlib.Path.cwd()
        try:
            import os

            os.chdir(tempfile.gettempdir())
            self.assertEqual(nine.model_path(), before)
        finally:
            import os

            os.chdir(original)

    def test_explicit_dir_still_honoured(self) -> None:
        self.assertEqual(
            nine.model_path("/tmp/somewhere"), pathlib.Path("/tmp/somewhere") / nine.MODEL_FILENAME
        )


class DebugDirTest(unittest.TestCase):
    """失败现场：既有落盘逻辑早就写好了，但从来没人传 debug_dir。"""

    def test_reset_debug_dir_clears_previous_files_only(self) -> None:
        folder = make_case_dir()
        self.addCleanup(__import__("shutil").rmtree, folder, True)
        (folder / "attempt1-101010-grid.png").write_bytes(b"old")
        (folder / "attempt1-101010.json").write_text("{}", encoding="utf-8")
        keep = folder / "keep-me.txt"
        keep.write_text("hi", encoding="utf-8")
        (folder / "sub").mkdir()

        nine.reset_debug_dir(folder)

        self.assertFalse((folder / "attempt1-101010-grid.png").exists())
        self.assertFalse((folder / "attempt1-101010.json").exists())
        self.assertTrue(keep.exists(), "不该删掉非本次生成的其它文件")
        self.assertTrue((folder / "sub").is_dir())

    def test_solve_local_passes_a_debug_dir(self) -> None:
        from miyouqian.core import captcha

        captured: dict = {}

        def fake_solve(gt, challenge, **kwargs):
            captured.update(kwargs)
            captured["gt"] = gt
            return ("validate", "passed-challenge")

        with mock.patch.object(captcha, "_local_matcher", lambda channel, emit: object()), \
             mock.patch.object(nine, "solve", fake_solve):
            result = captcha._solve_local(
                {"provider": "local", "headless": True, "max_attempts": 5},
                {},
                "gt-1",
                "challenge-1",
                None,
            )

        self.assertIsNotNone(result)
        self.assertIn("debug_dir", captured, "nine.solve 没收到 debug_dir，失败时就没有现场图")
        self.assertTrue(str(captured["debug_dir"]).strip())


class WordingTest(unittest.TestCase):
    """日志里别再出现「正在调用本地识别识别」。"""

    def test_no_double_recognition_word(self) -> None:
        root = pathlib.Path(nine.__file__).resolve().parents[2]
        for name in ("tasks/bbs.py", "tasks/games.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("{provider}识别(", text, f"{name} 还在拼「识别识别」")

    def test_dead_crop_helper_is_gone(self) -> None:
        self.assertFalse(hasattr(nine, "crop"), "nine.crop 是死代码，应当删掉")


if __name__ == "__main__":
    unittest.main()
