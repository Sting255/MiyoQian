# -*- coding: utf-8 -*-
"""极验三代「九宫格点选」本地求解。

思路：不去逆向 w 参数的加密与行为轨迹，而是用本机 Chrome 加载极验自己的 JS，
由极验前端计算校验数据，我们只负责「判断点哪几格」和「把鼠标点上去」。
这样行为数据天然是极验自己生成的，抗改版能力最强。
"""

from __future__ import annotations

import base64
import functools
import http.server
import json
import os
import pathlib
import random
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from typing import Any

import websocket
from loguru import logger

HARNESS = pathlib.Path(__file__).with_name("harness.html")
PROFILE_PREFIX = "myq-gt-"
CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
)


def cleanup_stale_profiles() -> int:
    """清掉上次残留的临时 profile，返回清掉的个数。

    每次解验证码都会新建一个临时 profile，正常会在 `Browser.close()` 里删掉。
    万一下次没删干净（比如浏览器卡死、CDP 连不上），服务启动时在这里兜底
    清扫一遍，避免进程和磁盘一起越积越多。正被占用的会跳过，留给下次。
    """
    root = pathlib.Path(tempfile.gettempdir())
    removed = 0
    for path in root.glob(f"{PROFILE_PREFIX}*"):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def find_browser() -> str:
    for path in CHROME_CANDIDATES:
        if pathlib.Path(path).exists():
            return path
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("未找到可用的 Chrome / Edge 浏览器")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class CdpPage:
    """极简 CDP 客户端，够用即可。"""

    def __init__(self, ws_url: str, timeout: float = 30.0) -> None:
        self.ws = websocket.create_connection(ws_url, timeout=timeout)
        self._next_id = 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._next_id += 1
        message_id = self._next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        while True:
            data = json.loads(self.ws.recv())
            if data.get("id") != message_id:
                continue
            if "error" in data:
                raise RuntimeError(f"CDP {method} 失败: {data['error']}")
            return data.get("result") or {}

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass

    def evaluate(self, expression: str) -> Any:
        result = self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=True,
        )
        outcome = result.get("result") or {}
        if result.get("exceptionDetails"):
            raise RuntimeError(f"页面脚本异常: {result['exceptionDetails']}")
        return outcome.get("value")

    def wait_for(self, expression: str, timeout: float = 30.0, interval: float = 0.4) -> Any:
        deadline = time.time() + timeout
        last: Any = None
        while time.time() < deadline:
            try:
                last = self.evaluate(expression)
            except websocket.WebSocketException:
                # websocket 已经断了（浏览器退出/被杀），再轮询只是白等，
                # 必须立刻把连接层错误抛出去，而不是报一个误导性的超时
                raise RuntimeError(f"CDP 连接已断开: {expression}") from None
            except Exception:
                # 页面脚本本身还没就绪（元素不存在、JS 报错等）——这才是要等的情况
                last = None
            if last:
                return last
            time.sleep(interval)
        raise TimeoutError(f"等待超时: {expression}（最后取值 {last!r}）")

    def click(self, x: float, y: float, settle: float = 0.35) -> None:
        for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
            self.call(
                "Input.dispatchMouseEvent",
                type=event_type,
                x=round(x, 2),
                y=round(y, 2),
                button="left",
                clickCount=1 if event_type != "mouseMoved" else 0,
                buttons=1 if event_type == "mousePressed" else 0,
            )
            time.sleep(random.uniform(0.03, 0.09))
        time.sleep(settle)

    def screenshot(self, path: str | pathlib.Path) -> None:
        result = self.call("Page.captureScreenshot", format="png")
        pathlib.Path(path).write_bytes(base64.b64decode(result["data"]))

    def screenshot_into(self, stream: Any) -> None:
        result = self.call("Page.captureScreenshot", format="png")
        stream.write(base64.b64decode(result["data"]))
        stream.seek(0)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:  # noqa: D102
        return


class HarnessServer:
    """把 harness.html 用 http 提供出去，避免 file:// 来源带来的限制。"""

    def __init__(self, directory: pathlib.Path, attempts: int = 5) -> None:
        # free_port() 是先 bind 再 close 拿到的端口，中间存在被别人抢走的窗口，
        # 所以这里换端口重试几次，而不是让一次偶发占用把整次识别搞失败。
        last_error: OSError | None = None
        for _ in range(max(attempts, 1)):
            self.port = free_port()
            handler = functools.partial(QuietHandler, directory=str(directory))
            try:
                self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), handler)
            except OSError as exc:
                last_error = exc
                continue
            self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
            self.thread.start()
            return
        raise OSError(f"harness 服务找不到可用端口: {last_error}")

    def url(self, gt: str, challenge: str) -> str:
        query = urllib.parse.urlencode(
            {"gt": gt, "challenge": challenge, "t": int(time.time() * 1000)}
        )
        return f"http://127.0.0.1:{self.port}/harness.html?{query}"

    def close(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


class Browser:
    """启动 / 关闭一个独立的浏览器实例。"""

    def __init__(self, headless: bool = True, port: int | None = None) -> None:
        self.headless = headless
        self.port = port or free_port()
        self.proc: subprocess.Popen | None = None
        self.page: CdpPage | None = None
        self.profile: pathlib.Path | None = None
        self.server: HarnessServer | None = None
        self.log_path: pathlib.Path | None = None
        self._log: Any = None
        try:
            # 顺序有讲究：先拿临时 profile，再起 harness 服务和日志文件。
            # 任何一步失败都要把已经占用的资源还回去，否则会留下
            # profile 目录 + harness 线程 + 一个被占用的端口（见 _cleanup_partial）。
            self.profile = pathlib.Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX))
            self.log_path = self.profile.with_suffix(".log")
            self._log = self.log_path.open("wb")
            self.server = HarnessServer(HARNESS.parent)
        except Exception:
            self._cleanup_partial()
            raise

    def _cleanup_partial(self) -> None:
        """构造中途失败时释放已占用的资源。"""
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None
        if self.server is not None:
            try:
                self.server.close()
            except Exception:
                pass
            self.server = None
        if self.profile is not None:
            shutil.rmtree(self.profile, ignore_errors=True)

    @staticmethod
    def _popen_kwargs(platform: str | None = None) -> dict[str, Any]:
        """启动浏览器时额外的 Popen 参数。

        Linux / macOS 上 `creationflags` 会直接抛
        `ValueError: creationflags is only supported on Windows platforms`，
        所以只在 Windows 传（这里带参数是为了不依赖 os.name 就能测）。
        """
        name = os.name if platform is None else platform
        if name != "nt":
            return {}
        # CREATE_NEW_PROCESS_GROUP：让浏览器独立于父进程的 Ctrl+C 等信号。
        # 不要加 CREATE_NO_WINDOW / DETACHED_PROCESS，两者与 headless 同用时
        # 会让浏览器起不来（表现为调试端口一直不监听）。
        return {"creationflags": 0x00000200}

    def start(self, attempts: int = 2) -> CdpPage:
        """启动浏览器并连上调试端口；失败会换端口重试一次。"""
        last_error: Exception | None = None
        for attempt in range(1, max(attempts, 1) + 1):
            try:
                return self._start_once()
            except Exception as exc:
                last_error = exc
                self._kill()
                if attempt < attempts:
                    # 换一个端口和 profile 再来一次，能绕过偶发的启动失败
                    self.port = free_port()
                    if self.profile is not None:
                        shutil.rmtree(self.profile, ignore_errors=True)
                    self.profile = pathlib.Path(tempfile.mkdtemp(prefix=PROFILE_PREFIX))
                    if self._log is not None:
                        try:
                            self._log.close()
                        except Exception:
                            pass
                    self.log_path = self.profile.with_suffix(".log")
                    self._log = self.log_path.open("wb")
                    time.sleep(1.0)
        raise RuntimeError(f"浏览器启动失败：{last_error}") from last_error

    def _start_once(self) -> CdpPage:
        command = [
            find_browser(),
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            "--mute-audio",
            "--disable-gpu",
            "--remote-allow-origins=*",
            "--window-size=1200,900",
        ]
        if self.headless:
            command.append("--headless=new")
        command.append("about:blank")
        self.proc = subprocess.Popen(
            command,
            stdout=self._log,
            stderr=self._log,
            **self._popen_kwargs(),
        )
        self._wait_devtools()
        self.page = CdpPage(self._target_ws())
        self.page.call("Page.enable")
        self.page.call("Runtime.enable")
        return self.page

    def _wait_devtools(self, timeout: float = 30.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/version", timeout=2
                ) as response:
                    if response.status == 200:
                        return
            except Exception:
                # 进程已经退出就不用再等了，直接把浏览器自己的报错带出去
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError(
                        f"浏览器启动后立即退出（返回码 {self.proc.returncode}）：{self._tail()}"
                    ) from None
                time.sleep(0.3)
        raise RuntimeError(f"浏览器调试端口未就绪（{timeout:.0f} 秒超时）：{self._tail()}")

    def _tail(self, limit: int = 400) -> str:
        """取浏览器 stderr 尾部，排查启动失败时很有用。"""
        if self._log is None or self.log_path is None:
            return "无输出"
        try:
            self._log.flush()
            text = self.log_path.read_text(encoding="utf-8", errors="replace").strip()
        except (OSError, ValueError):
            return "无输出"
        return text[-limit:] if text else "无输出"

    def _target_ws(self, timeout: float = 15.0) -> str:
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=3
                ) as response:
                    targets = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                # 端口刚监听时偶发连接被拒，这里必须重试 ——
                # 原来没包 try，一次瞬时错误就直接抛出，timeout 形同虚设。
                last_error = exc
                if self.proc is not None and self.proc.poll() is not None:
                    raise RuntimeError(
                        f"浏览器在读取页面列表时退出（返回码 {self.proc.returncode}）：{self._tail()}"
                    ) from None
                time.sleep(0.3)
                continue
            for target in targets:
                if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                    return str(target["webSocketDebuggerUrl"])
            time.sleep(0.3)
        raise TimeoutError(f"未找到可用的浏览器页面（{timeout:.0f} 秒超时，最后错误：{last_error}）")

    def open(self, url: str) -> None:
        if self.page is None:
            raise RuntimeError("浏览器尚未启动")
        self.page.call("Page.navigate", url=url)
        self.page.wait_for("document.readyState === 'complete'", timeout=25)

    def _port_alive(self) -> bool:
        """调试端口还能响应就说明浏览器还活着。"""
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/version", timeout=1.5
            ) as response:
                return response.status == 200
        except Exception:
            return False

    def _close_via_cdp(self) -> bool:
        """让浏览器自己退出（CDP Browser.close）。

        必须连 **browser 级** 的 websocket（`/json/version` 里那个），不是页面级的。
        浏览器会正常走关闭流程，主动释放 profile 里的文件句柄，
        所以紧接着就能把临时目录删掉。
        """
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/json/version", timeout=5
            ) as response:
                version = json.loads(response.read().decode("utf-8"))
            ws_url = str(version.get("webSocketDebuggerUrl") or "")
        except Exception:
            return False
        if not ws_url:
            return False
        try:
            socket = websocket.create_connection(ws_url, timeout=8)
        except Exception:
            return False
        try:
            socket.send(json.dumps({"id": 1, "method": "Browser.close"}))
            try:
                socket.settimeout(3)
                socket.recv()
            except Exception:
                pass
            return True
        except Exception:
            return False
        finally:
            try:
                socket.close()
            except Exception:
                pass

    def _wait_exit(self, timeout: float = 8.0) -> bool:
        """等浏览器真正退出：调试端口不再响应就算退干净了。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not self._port_alive():
                return True
            time.sleep(0.2)
        return False

    def _force_kill(self) -> None:
        """兜底强杀。

        注意 `self.proc` 只是 Chrome 的「启动器」进程，它派生真正的 browser
        进程之后自己就退出了，所以这里往往杀不到什么——它只用于应付
        CDP 关闭失败的情形，真没杀干净就交给下次启动时的清理兜底。
        """
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
            )
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self.proc.wait(timeout=5)
        except Exception:
            pass

    def _kill(self) -> None:
        """关闭浏览器。

        优先用 CDP 的 `Browser.close` 让它自己优雅退出——这样文件句柄立刻
        释放，紧接着就能删掉临时 profile。

        以前只靠 taskkill，而 taskkill 拿到的是「启动器」PID，杀不到真正的
        Chrome 进程树（那棵树挂在另一个 PID 下），于是每次 close 都留下
        一整组 8 个进程锁着临时目录，进程和磁盘一起越积越多。
        """
        if self._close_via_cdp() and self._wait_exit():
            return
        # CDP 优雅关闭失败 → 只能强杀，而强杀往往杀不到真正的 Chrome 进程树
        # （启动器 PID 早已退出）。这里必须留痕，否则进程/磁盘泄漏是悄无声息的。
        logger.bind(component="captcha").warning(
            "CDP 关闭浏览器失败，回退强杀（可能残留进程与临时目录，下次启动会自动清理）"
        )
        self._force_kill()

    def close(self) -> None:
        if self.page:
            try:
                self.page.close()
            except Exception:
                pass
            self.page = None
        self._kill()
        if self._log is not None:
            try:
                self._log.close()
            except Exception:
                pass
            self._log = None
        # 走 CDP 优雅关闭后句柄已释放，一两次就能删掉；
        # 留下重试是为了兜底：强杀之后可能还有短暂的文件占用。
        if self.profile is not None:
            for _ in range(5):
                shutil.rmtree(self.profile, ignore_errors=True)
                if not self.profile.exists():
                    break
                time.sleep(0.4)
        if self.log_path is not None:
            try:
                self.log_path.unlink()
            except OSError:
                pass
        if self.server is not None:
            self.server.close()
            self.server = None

    def open_harness(self, gt: str, challenge: str) -> None:
        if self.server is None:
            raise RuntimeError("harness 服务未就绪")
        self.open(self.server.url(gt, challenge))
