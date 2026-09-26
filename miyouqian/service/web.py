# -*- coding: utf-8 -*-
"""本地 Web 控制台。"""

from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import mimetypes
import pathlib
import random
import secrets
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, unquote, urlparse

import qrcode

from ..auth.login import AigisRequired, CaptchaLogin, QRLogin, _QrRefreshed
from ..core import captcha as captcha_mod
from ..core import cookies, crypto
from ..core.config import load_config, log_path, normalize_config, save_config, validate_unique_account_uids
from ..core.http import ApiClient, shop_client
from ..core.geetest.browser import cleanup_stale_profiles
from ..core.geetest.nine import self_check as captcha_self_check
from ..core.ipcheck import probe_links
from ..core.logs import append_log, configure_logger, format_line, print_startup_banner
from ..tasks.shop_exchange import ShopExchange
from .exchange_scheduler import ExchangeScheduler
from .notifier import build_push_title, push_run_result, send_push, send_exchange_push, push_channels
from .runner import run_tasks
from .scheduler import DailyScheduler

WEB_ROOT = pathlib.Path(__file__).resolve().parents[1] / "webui"

# 「测试推送」用的示例日志：让用户在真正跑任务前就能看到推送长什么样
TEST_PUSH_SAMPLE = """# 账号 1/1: 示例账号
游戏社区签到汇总：成功 3，失败 0，跳过 0
云游戏签到汇总：成功 0，失败 0，跳过 0
米游币社区任务
米游币任务汇总：成功 1，失败 0，跳过 0，今日总共可获得 40，实际已获得 40，本次新增 40
社区任务结束：今日已得 40，还能获得 0，当前总计 352"""

# ---------------------------------------------------------------------------
# 密码认证工具
# ---------------------------------------------------------------------------
AUTH_COOKIE = "myq_token"

# 脱敏占位符：GET /api/config 用它代替真值。
# 前端是「整体取回 → 改动 → 整体 POST 回来」，所以保存时看到这个值
# （或空值）就按「未修改」处理，从服务端旧配置里把真值填回去。
MASKED_SECRET = "__MYQ_MASKED__"
# 账号凭证：stuid 故意不掩码——界面要显示 UID，而且它还是还原时的匹配键
MASKED_ACCOUNT_FIELDS = ("cookie", "stoken", "mid")
MASKED_PUSH_FIELDS = ("token", "webhook", "secret", "access_token", "smtp_password")
MASKED_CAPTCHA_FIELDS = ("userkey",)


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def is_hashed_password(value: str) -> bool:
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def check_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    return secrets.compare_digest(hash_password(password), stored_hash)


def is_external_host(host: str) -> bool:
    """这个监听地址是不是「本机以外也能访问到」。

    注意空字符串要当作「没提供」而不是外网，否则 is_external_host("") 会返回 True。
    """
    text = str(host or "").strip().lower()
    if not text:
        return False
    return text not in ("127.0.0.1", "localhost", "::1", "[::1]", "0:0:0:0:0:0:0:1")


class WebApp:
    def __init__(self, config_path: pathlib.Path, bound_host: str = "") -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        # 实际监听地址（serve() 传进来）。
        # 判断要不要密码必须看这个值，不能只看 config 里的 web.host：
        # Dockerfile 用 `--host 0.0.0.0` 启动，而 config.example.yaml 里写的是 127.0.0.1，
        # 只看 config 会让控制台在局域网上完全不需要认证。
        self.bound_host = str(bound_host or "")
        self._ensure_password_hashed()
        self.log_file = log_path(config_path, self.config)
        configure_logger(self.log_file)
        self.lock = threading.RLock()
        self.logs: list[str] = []
        self.login_state: dict[str, Any] = {"running": False, "status": "idle"}
        self._qr_refresh = threading.Event()
        self._login_cancel = threading.Event()
        self._login_generation = 0
        self.scheduler = DailyScheduler(self.config, self.run_all, lambda message: self.log(message, "scheduler"))
        self.exchange_scheduler = ExchangeScheduler(
            self.config,
            self.run_shop_exchange_plan,
            lambda message: self.log(message, "exchange"),
        )
        self._sessions: dict[str, float] = {}

    def _ensure_password_hashed(self) -> None:
        web = self.config.get("web", {})
        password = str(web.get("password", ""))
        if password and not is_hashed_password(password):
            web["password"] = hash_password(password)
            save_config(self.config_path, self.config)

    @property
    def need_auth(self) -> bool:
        """实际监听地址或配置声明了外网，就必须认证（宁严勿松）。"""
        web = self.config.get("web") or {}
        config_host = str(web.get("host", "127.0.0.1"))
        return is_external_host(self.bound_host) or is_external_host(config_host)

    @property
    def password_is_set(self) -> bool:
        return bool(self.config.get("web", {}).get("password", ""))

    def _create_session(self) -> str:
        token = secrets.token_hex(32)
        with self.lock:
            self._sessions[token] = True
        return token

    def _check_session(self, token: str) -> bool:
        if not token:
            return False
        with self.lock:
            return token in self._sessions

    def auth_setup(self, password: str) -> str:
        if not self.need_auth:
            raise ValueError("当前为内网模式，无需设置密码")
        if self.password_is_set:
            raise ValueError("密码已设置，不能重复设置")
        if len(password) < 4:
            raise ValueError("密码长度至少 4 位")
        with self.lock:
            self.config.setdefault("web", {})["password"] = hash_password(password)
            save_config(self.config_path, self.config)
        self.log("已设置外网访问密码", "auth")
        return self._create_session()

    def auth_login(self, password: str) -> str:
        if not self.need_auth:
            raise ValueError("当前为内网模式，无需登录")
        stored_hash = self.config.get("web", {}).get("password", "")
        if not stored_hash:
            raise ValueError("密码未设置，请先设置密码")
        if not check_password(password, stored_hash):
            raise ValueError("密码错误")
        return self._create_session()

    def auth_status(self) -> dict[str, Any]:
        return {
            "need_auth": self.need_auth,
            "password_set": self.password_is_set,
        }

    def start(self) -> None:
        # 解验证码会建临时 profile 目录，正常用完就删；万一有没删干净的，
        # 在这里兜底清扫，避免进程和磁盘一起越积越多。
        try:
            stale = cleanup_stale_profiles()
            if stale:
                self.log(f"已清理上次残留的验证码临时目录 {stale} 个", "startup")
        except Exception as exc:
            self.log(f"清理验证码临时目录失败: {exc}", "startup")
        self.scheduler.start()
        self.exchange_scheduler.start()

    def stop(self) -> None:
        self.scheduler.stop()
        self.exchange_scheduler.stop()

    def log(self, message: str, component: str = "web") -> None:
        line = format_line(message, component)
        with self.lock:
            self.logs.append(line)
            self.logs = self.logs[-500:]
            log_file = self.log_file
        append_log(log_file, line, component=component)

    def run_all(self, stop_event: Any = None) -> list[str]:
        with self.lock:
            config = self.config
        try:
            self.log("任务编排开始", "task")
            lines = run_tasks(
                config,
                str(self.config_path),
                emit_component=lambda message, component: self.log(message, component),
                stop_event=stop_event,
            )
        except Exception as exc:
            push_result = send_push(config, "米游签任务失败", str(exc), success=False)
            if push_result:
                self.log(push_result, "push")
            raise
        self.log("任务编排完成，准备发送推送", "task")
        _success, push_result = push_run_result(config, lines)
        if push_result:
            self.log(push_result, "push")
        return []

    def stop_run(self) -> bool:
        """停止正在执行的任务。"""
        if not self.scheduler.stop_run():
            return False
        self.log("已发送停止指令，正在中断当前任务", "scheduler")
        return True

    def test_push_channels(self) -> str:
        with self.lock:
            config = copy.deepcopy(self.config)
        channels = push_channels(config.get("push") or {})
        if not channels:
            raise ValueError("请先启用至少一个推送通道")
        lines = [line for line in TEST_PUSH_SAMPLE.strip().splitlines() if line.strip()]
        result = send_push(
            config,
            "【推送测试】" + build_push_title(lines, True),
            "\n".join(lines),
            success=True,
        )
        if result:
            self.log(result, "push")
        return result or "推送测试已完成"

    def check_ip(self) -> dict[str, Any]:
        """立即检测一次出口 IP：国内直连链路 + 境外链路分别查。"""
        with self.lock:
            guard = copy.deepcopy(self.config.get("ip_guard") or {})
        report = probe_links(domestic=guard.get("endpoints") or None)
        result = report.to_dict()
        if report.split:
            summary = f"分流代理：{report.reason}"
        elif report.blocked:
            summary = f"境外出口：{report.reason}"
        elif report.mainland is True:
            summary = f"中国大陆：{report.domestic.label}"
        else:
            summary = f"属地未知：{report.reason}"
        self.log(f"出口 IP 检测：{summary}", "ip")
        return result

    def check_captcha_env(self) -> dict[str, Any]:
        """真实拉起一次 Chrome + harness 页面，验证本地识别环境可用。

        沙箱里测不了这个（命名管道被禁），必须放到服务环境里跑；
        不碰账号、不下载模型，只验证「浏览器起得来、页面打得开」。
        """
        with self.lock:
            channel = copy.deepcopy(captcha_mod._active_channel(self.config))
        headless = bool(channel.get("headless", True)) if channel else True
        result = captcha_self_check(headless=headless)
        if result.get("ok"):
            self.log(
                f"验证码环境自检通过（{result.get('browser', '?')}，耗时 {result.get('seconds', '?')} 秒）",
                "captcha",
            )
        else:
            self.log(f"验证码环境自检失败：{result.get('error', '未知错误')}", "captcha")
        return result

    def status(self) -> dict[str, Any]:
        with self.lock:
            logs = list(self.logs[-300:])
            login_state = dict(self.login_state)
            shop_exchange = copy.deepcopy(self.config.get("shop_exchange", {}))
        return {
            "scheduler": self.scheduler.status(),
            "exchange_scheduler": self.exchange_scheduler.status(),
            "shop_exchange": shop_exchange,
            "login": login_state,
            "logs": logs,
        }

    def get_config(self) -> dict[str, Any]:
        with self.lock:
            return public_config(self.config)

    def set_config(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("配置必须是 JSON 对象")
        normalize_config(payload)
        validate_unique_account_uids(payload)
        with self.lock:
            old_config = copy.deepcopy(self.config)
            # 脱敏是成对的：响应里掩掉的真值必须在这里先填回来，
            # 否则「保存一次配置」就等于把所有凭证和访问密码清空。
            restore_masked_secrets(old_config, payload)
            preserve_push_channel_secrets(old_config, payload)
            preserve_web_password(old_config, payload)
            normalize_config(payload)
            changes = diff_config(old_config, payload)
            self.config = payload
            save_config(self.config_path, self.config)
            self.log_file = log_path(self.config_path, self.config)
            configure_logger(self.log_file)
            self.scheduler.reload(self.config)
            self.exchange_scheduler.reload(self.config)
        visible_changes = [change for change in changes if should_log_config_change(*change)]
        if visible_changes:
            self.log(f"配置已保存，共 {len(visible_changes)} 项变更", "config")
            for path, old_value, new_value in visible_changes[:50]:
                self.log(
                    f"配置项变更 {path}: {format_config_value(path, old_value)} -> {format_config_value(path, new_value)}",
                    "config",
                )
            if len(visible_changes) > 50:
                self.log(f"配置项变更过多，已省略 {len(visible_changes) - 50} 项", "config")
        else:
            self.log("配置已保存，未检测到配置项变化", "config")

    def reroll_device(self, preset_name: str = "") -> dict[str, Any]:
        """更换设备指纹：挑一个机型预设并重新生成设备 id 与 fp。"""
        with self.lock:
            device = self.config.setdefault("device", {})
            presets = [item for item in (device.get("presets") or []) if isinstance(item, dict)]
            chosen: dict[str, Any] | None = None
            if preset_name:
                chosen = next(
                    (item for item in presets if str(item.get("name") or "") == preset_name),
                    None,
                )
                if chosen is None:
                    raise ValueError(f"未找到机型预设: {preset_name}")
            else:
                current = str(device.get("name") or "")
                candidates = [item for item in presets if str(item.get("name") or "") != current]
                chosen = random.choice(candidates or presets) if (candidates or presets) else None
            if chosen:
                device["name"] = str(chosen.get("name") or device.get("name") or "")
                device["model"] = str(chosen.get("model") or device.get("model") or "")
            device["id"] = crypto.device_id()
            device["fp"] = crypto.device_fp()
            result = {
                "id": str(device["id"]),
                "fp": str(device["fp"]),
                "name": str(device.get("name") or ""),
                "model": str(device.get("model") or ""),
            }
            save_config(self.config_path, self.config)
        self.log(f"设备指纹已更换：{result['name']}（{result['model']}）", "config")
        return result

    def shop_goods(self, game: str = "") -> dict[str, Any]:
        game_label = game or "全部分区"
        self.log(f"开始获取商品列表: {game_label}", "exchange")
        with self.lock:
            config = copy.deepcopy(self.config)
        try:
            with shop_client(timeout=20.0) as client:
                result = ShopExchange(client, config, emit=lambda message: self.log(message, "exchange")).goods(game=game)
        except Exception as exc:
            self.log(f"商品列表获取失败: {game_label}，{exc}", "exchange")
            raise
        goods_count = len(result.get("goods") or [])
        self.log(f"商品列表获取完成: {game_label}，共 {goods_count} 个商品", "exchange")
        return result

    def shop_good_detail(self, goods_id: str) -> dict[str, Any]:
        self.log(f"开始获取商品详情: {goods_id}", "exchange")
        with self.lock:
            config = copy.deepcopy(self.config)
        try:
            with shop_client(timeout=20.0) as client:
                result = ShopExchange(client, config).good_detail(goods_id)
        except Exception as exc:
            self.log(f"商品详情获取失败: {goods_id}，{exc}", "exchange")
            raise
        self.log(f"商品详情获取完成: {result.get('goods_name') or goods_id}", "exchange")
        return result

    def ensure_shop_device_fp(self) -> dict[str, Any]:
        self.log("开始预获取商品兑换 device_fp", "exchange")
        with self.lock:
            config = copy.deepcopy(self.config)
        try:
            with shop_client(timeout=20.0) as client:
                device_fp = ShopExchange(client, config).fetch_device_fp()
        except Exception as exc:
            self.log(f"商品兑换 device_fp 获取失败: {exc}", "exchange")
            raise
        with self.lock:
            self.config.setdefault("device", {})["fp"] = device_fp
            save_config(self.config_path, self.config)
        self.log("已预获取商品兑换 device_fp", "exchange")
        return {"device_fp": device_fp}

    def shop_account_meta(self, account_index: int, game_biz: str = "") -> dict[str, Any]:
        account = self._account_by_index(account_index)
        account_name = display_account_name(account)
        self.log(f"开始获取兑换账号信息: {account_name}，game_biz={game_biz or '无'}", "exchange")
        with self.lock:
            config = copy.deepcopy(self.config)
        with shop_client(timeout=20.0) as client:
            shop = ShopExchange(client, config, account)
            result: dict[str, Any] = {"points": {}, "addresses": [], "roles": []}
            try:
                result["points"] = shop.points()
            except Exception as exc:
                result["points_error"] = str(exc)
                self.log(f"米游币余额获取失败: {account_name}，{exc}", "exchange")
            try:
                result["addresses"] = shop.addresses()
            except Exception as exc:
                result["addresses_error"] = str(exc)
                self.log(f"收货地址获取失败: {account_name}，{exc}", "exchange")
            if game_biz:
                try:
                    result["roles"] = shop.roles(game_biz)
                except Exception as exc:
                    result["roles_error"] = str(exc)
                    self.log(f"游戏角色获取失败: {account_name}，{game_biz}，{exc}", "exchange")
                    raise
            self.log(
                f"兑换账号信息获取完成: {account_name}，地址 {len(result.get('addresses') or [])} 个，角色 {len(result.get('roles') or [])} 个",
                "exchange",
            )
            return result

    def shop_exchange_once(self, plan: dict[str, Any], on_progress: Callable[[int, str], None] | None = None) -> dict[str, Any]:
        account = self._account_by_index(int(plan.get("account_index") or 0))
        account_name = display_account_name(account)
        goods_name = str(plan.get("goods_name") or plan.get("goods_id") or "未知商品")
        self.log(f"开始商品兑换: {goods_name}，账号 {account_name}", "exchange")
        if not str(plan.get("device_fp") or "").strip():
            raise ValueError("兑换计划缺少 device_fp，请重新添加计划")
        with self.lock:
            config = copy.deepcopy(self.config)
        try:
            with shop_client(timeout=15.0) as client:
                result = ShopExchange(
                    client,
                    config,
                    account,
                    emit=lambda message: self.log(message, "exchange"),
                ).exchange_with_retry(plan, on_progress=on_progress)
        except Exception as exc:
            self.log(f"商品兑换请求异常: {goods_name}，账号 {account_name}，{exc}", "exchange")
            raise
        summary = f"{result.get('message', '未知结果')}({result.get('retcode')})，请求 {result.get('attempt', 1)} 次"
        self.log(f"商品兑换结束: {goods_name}，账号 {account_name}，{summary}", "exchange")
        return result

    def shop_exchange_plan_once(self, plan_index: int) -> dict[str, Any]:
        with self.lock:
            plans = self.config.get("shop_exchange", {}).get("plans") or []
            shop_config = self.config.get("shop_exchange", {})
            if plan_index < 0 or plan_index >= len(plans):
                raise ValueError("兑换计划不存在")
            plan = copy.deepcopy(plans[plan_index])
            goods_name = plan.get("goods_name") or plan.get("goods_id")
        self.log(f"开始手动执行商品兑换计划 {plan_index + 1}: {goods_name}", "exchange")
        self.exchange_scheduler.update_progress(plan_index, attempt=0, message="准备兑换")
        try:
            result = self.shop_exchange_once(
                plan,
                on_progress=lambda attempt, msg: self.exchange_scheduler.update_progress(
                    plan_index, attempt=attempt, message=msg
                ),
            )
        except Exception as exc:
            summary = f"异常: {exc}"
            with self.lock:
                plans = self.config.get("shop_exchange", {}).get("plans") or []
                if plan_index < len(plans):
                    plans[plan_index]["last_result"] = summary
                    plans[plan_index]["last_run"] = datetime.now().isoformat(timespec="seconds")
                    plans[plan_index]["last_attempt_key"] = f"manual:{datetime.now().isoformat(timespec='seconds')}"
                    save_config(self.config_path, self.config)
                    self.exchange_scheduler.reload(self.config)
            if shop_config.get("push", False):
                self._send_exchange_push(goods_name, {"ok": False, "message": summary}, plan)
            raise
        finally:
            self.exchange_scheduler.clear_progress(plan_index)
        summary = f"{result.get('message', '未知结果')}({result.get('retcode')})，请求 {result.get('attempt', 1)} 次"
        with self.lock:
            plans = self.config.get("shop_exchange", {}).get("plans") or []
            if plan_index < len(plans):
                plans[plan_index]["last_result"] = summary
                plans[plan_index]["last_run"] = datetime.now().isoformat(timespec="seconds")
                plans[plan_index]["last_attempt_key"] = f"manual:{datetime.now().isoformat(timespec='seconds')}"
                if result.get("ok"):
                    plans[plan_index]["enable"] = False
                save_config(self.config_path, self.config)
                self.exchange_scheduler.reload(self.config)
        self.log(f"手动商品兑换计划完成 {plan_index + 1}: {goods_name}，{summary}", "exchange")

        # 发送兑换结果推送
        if shop_config.get("push", False):
            self._send_exchange_push(goods_name, result, plan)

        return result

    def run_shop_exchange_plan(self, plan_index: int) -> None:
        with self.lock:
            plans = self.config.get("shop_exchange", {}).get("plans") or []
            shop_config = self.config.get("shop_exchange", {})
            if plan_index < 0 or plan_index >= len(plans):
                raise ValueError("兑换计划不存在")
            plan = copy.deepcopy(plans[plan_index])
            goods_name = plan.get("goods_name") or plan.get("goods_id")
            exchange_at = int(plan.get("exchange_at") or 0)
            plans[plan_index]["last_attempt_key"] = f"{plan.get('goods_id', '')}:{exchange_at}"
            plans[plan_index]["last_run"] = datetime.now().isoformat(timespec="seconds")
            save_config(self.config_path, self.config)
        self.log(f"开始执行商品兑换计划: {goods_name}", "exchange")
        try:
            result = self.shop_exchange_once(
                plan,
                on_progress=lambda attempt, msg: self.exchange_scheduler.update_progress(
                    plan_index, attempt=attempt, message=msg
                ),
            )
        except Exception as exc:
            summary = f"异常: {exc}"
            with self.lock:
                plans = self.config.get("shop_exchange", {}).get("plans") or []
                if plan_index < len(plans):
                    plans[plan_index]["last_result"] = summary
                    plans[plan_index]["last_run"] = datetime.now().isoformat(timespec="seconds")
                    save_config(self.config_path, self.config)
            self.exchange_scheduler.reload(self.config) # 移出锁外,防止嵌套等待
            if shop_config.get("push", False):
                self._send_exchange_push(goods_name, {"ok": False, "message": summary, "attempt": 0}, plan)
            self.exchange_scheduler.clear_progress(plan_index)
            raise
        summary = f"{result.get('message', '未知结果')}({result.get('retcode')})，请求 {result.get('attempt', 1)} 次"
        with self.lock:
            plans = self.config.get("shop_exchange", {}).get("plans") or []
            if plan_index < len(plans):
                plans[plan_index]["last_result"] = summary
                plans[plan_index]["last_run"] = datetime.now().isoformat(timespec="seconds")
                if result.get("ok"):
                    plans[plan_index]["enable"] = False
                save_config(self.config_path, self.config)
        self.exchange_scheduler.reload(self.config) #同上
        self.log(f"商品兑换计划完成: {goods_name}，{summary}", "exchange")

        # 发送兑换结果推送
        if shop_config.get("push", False):
            self._send_exchange_push(goods_name, result, plan)

        # 自动路径以前不清理实时进度，_progress 里的条目会一直留着（脏数据）
        self.exchange_scheduler.clear_progress(plan_index)

    def _send_exchange_push(self, goods_name: str, result: dict[str, Any], plan: dict[str, Any]) -> None:
        """发送商品兑换结果推送"""
        with self.lock:
            config = self.config
        try:
            is_success = result.get("ok", False)

            # 构建推送标题
            if is_success:
                title = "🎉 商品兑换成功"
            else:
                title = "❌ 商品兑换失败"

            # 从 account_index 获取账号信息，添加到 plan 中
            plan_with_account = dict(plan)
            account_index = plan.get("account_index")
            if account_index is not None:
                try:
                    account = self._account_by_index(int(account_index))
                    plan_with_account["account"] = display_account_name(account)
                except Exception:
                    plan_with_account["account"] = "未知账号"

            push_result = send_exchange_push(config, title, goods_name, result, plan_with_account, success=is_success)
            if push_result:
                self.log(f"商品兑换推送已发送: {push_result}", "exchange")
        except Exception as exc:
            self.log(f"商品兑换推送发送失败: {exc}", "exchange")

    def _account_by_index(self, account_index: int) -> dict[str, Any]:
        with self.lock:
            accounts = self.config.get("accounts") or []
            if account_index < 0 or account_index >= len(accounts):
                raise ValueError("请选择已登录账号")
            account = copy.deepcopy(accounts[account_index])
        if not str(account.get("cookie") or "").strip():
            raise ValueError("账号未登录或缺少 cookie")
        return account

    def start_login(
        self,
        account_index: int,
        timeout: int,
        account_payload: dict[str, Any] | None = None,
        draft: bool = False,
    ) -> None:
        with self.lock:
            if self.login_state.get("running"):
                raise RuntimeError("扫码登录正在进行")
            accounts = self.config.get("accounts") or []
            if not draft and (account_index < 0 or account_index >= len(accounts)):
                raise ValueError("请先添加账号")
            account_snapshot = dict(account_payload or {})
            if not draft:
                account_snapshot = dict(accounts[account_index])
            account_name = display_account_name(account_snapshot)
            self.login_state = {
                "running": True,
                "status": "starting",
                "account_index": account_index,
                "account": account_name,
                "draft": draft,
                "message": "正在生成二维码",
                "qr": "",
            }
            self._qr_refresh.clear()
            self._login_cancel.clear()
            self._login_generation += 1
            generation = self._login_generation
        thread = threading.Thread(
            target=self._login_worker,
            args=(account_index, timeout, account_snapshot, draft, generation),
            name="miyouqian-web-login",
            daemon=True,
        )
        thread.start()
        self.log(f"账号 {account_name} 开始扫码登录", "auth")

    def send_login_captcha(
        self,
        account_index: int,
        phone: str,
        account_payload: dict[str, Any] | None = None,
        draft: bool = False,
        aigis: str = "",
    ) -> dict[str, str]:
        phone = normalize_cn_phone(phone)
        account_snapshot, account_name = self._login_account_snapshot(account_index, account_payload, draft)
        with self.lock:
            if self.login_state.get("running"):
                raise RuntimeError("扫码登录正在进行")
            device = dict(self.config["device"])
        with ApiClient() as client:
            login = CaptchaLogin(client, str(device["id"]), str(device["fp"]),
                                 str(device.get("model") or "Mi 14"),
                                 str(device.get("name") or "Mihoyo Capture"))
            result = login.create_captcha(phone, aigis)
        self.log(f"账号 {account_name} 已发送验证码", "auth")
        return result

    def complete_login_captcha(
        self,
        account_index: int,
        phone: str,
        captcha: str,
        action_type: str,
        account_payload: dict[str, Any] | None = None,
        draft: bool = False,
        aigis: str = "",
    ) -> dict[str, Any]:
        phone = normalize_cn_phone(phone)
        captcha = str(captcha or "").strip()
        action_type = str(action_type or "").strip()
        if not captcha:
            raise ValueError("请输入短信验证码")
        if not action_type:
            raise ValueError("请先发送短信验证码")
        account_snapshot, account_name = self._login_account_snapshot(account_index, account_payload, draft)
        with self.lock:
            if self.login_state.get("running"):
                raise RuntimeError("扫码登录正在进行")
            device = dict(self.config["device"])
        with ApiClient() as client:
            login = CaptchaLogin(client, str(device["id"]), str(device["fp"]),
                                 str(device.get("model") or "Mi 14"),
                                 str(device.get("name") or "Mihoyo Capture"))
            token_data = login.login_by_mobile_captcha(phone, captcha, action_type, aigis)
            account_data = self._complete_login_data(login, token_data)
        account = self._save_login_account(account_index, account_snapshot, account_data, draft)
        account_name = display_account_name(account)
        if draft:
            self.log(f"账号 {account_name} 验证码登录成功，等待保存", "auth")
        else:
            self.log(f"账号 {account_name} 验证码登录成功，凭证已保存", "auth")
        return {
            "account_index": account_index,
            "draft": draft,
            "message": f"账号 {account_name} 登录成功" + ("，请保存账号" if draft else ""),
            "account_data": account_data,
        }

    def _login_account_snapshot(
        self,
        account_index: int,
        account_payload: dict[str, Any] | None,
        draft: bool,
    ) -> tuple[dict[str, Any], str]:
        with self.lock:
            accounts = self.config.get("accounts") or []
            if not draft and (account_index < 0 or account_index >= len(accounts)):
                raise ValueError("请先添加账号")
            account_snapshot = dict(account_payload or {})
            if not draft:
                account_snapshot = dict(accounts[account_index])
            account_name = display_account_name(account_snapshot)
        return account_snapshot, account_name

    def _complete_login_data(self, login: QRLogin | CaptchaLogin, token_data: dict[str, str]) -> dict[str, str]:
        account_data = {**token_data, **login.get_additional_tokens(token_data["stoken"], token_data["mid"])}
        account_data["cookie"] = cookies.build_cookie(
            account_data["stuid"],
            account_data["mid"],
            account_data["ltoken"],
            account_data["cookie_token"],
        )
        return account_data

    def _save_login_account(
        self,
        account_index: int,
        account_snapshot: dict[str, Any],
        account_data: dict[str, str],
        draft: bool,
    ) -> dict[str, Any]:
        with self.lock:
            new_uid = str(account_data.get("stuid") or "").strip()
            duplicate = find_duplicate_uid(self.config.get("accounts") or [], new_uid, None if draft else account_index)
            if duplicate is not None:
                raise ValueError(f"UID {new_uid} 已存在，不能重复添加同一账号")
            account = {**account_snapshot, **account_data}
            if not str(account.get("name") or "").strip():
                account["name"] = account_data["stuid"]
            if not draft:
                self.config["accounts"][account_index] = account
                save_config(self.config_path, self.config)
                self.log_file = log_path(self.config_path, self.config)
                configure_logger(self.log_file)
                self.scheduler.reload(self.config)
        return account

    def _login_worker(
        self,
        account_index: int,
        timeout: int,
        account_snapshot: dict[str, Any],
        draft: bool,
        generation: int,
    ) -> None:
        try:
            if self._login_cancel.is_set():
                return
            with self.lock:
                device = dict(self.config["device"])
                account_name = display_account_name(account_snapshot)
            with ApiClient() as client:
                login = QRLogin(client, str(device["id"]), str(device["fp"]),
                               str(device.get("model") or "Mi 14"),
                               str(device.get("name") or "Mihoyo Capture"))
                url, ticket = login.fetch()
                qr = make_qr_data_uri(url)
                with self.lock:
                    self.login_state.update(
                        {
                            "status": "waiting",
                            "message": "等待扫码确认",
                            "qr": qr,
                            "qr_url": url,
                        }
                    )
                self.log(f"账号 {account_name} 等待扫码登录", "auth")
                scan = self._wait_with_refresh(login, ticket, timeout, account_name)
                if self._login_cancel.is_set():
                    return
                with self.lock:
                    self.login_state.update({"status": "exchanging", "message": "正在获取完整凭证"})
                account_data = self._complete_login_data(login, scan)
                if self._login_cancel.is_set():
                    # 换 ltoken/cookie_token 期间用户点了取消：不能把凭证偷偷存下来
                    # （UI 已经显示「登录流程已取消」，行为必须一致）
                    self.log(f"账号 {account_name} 登录已取消，凭证未保存", "auth")
                    return
            account = self._save_login_account(account_index, account_snapshot, account_data, draft)
            with self.lock:
                account_name = display_account_name(account)
                if self._login_generation == generation:
                    payload: dict[str, Any] = {
                        "running": False,
                        "status": "success",
                        "message": f"账号 {account_name} 登录成功" + ("，请保存账号" if draft else ""),
                        "qr": "",
                    }
                    if draft:
                        # 只有草稿账号需要把凭证回传给浏览器（前端要拿它填进账号卡片再保存）。
                        # 非草稿的凭证已经落盘，没必要在 /api/status 里反复明文回传。
                        payload["account_data"] = account_data
                    self.login_state.update(payload)
            if draft:
                self.log(f"账号 {account_name} 登录成功，等待保存", "auth")
            else:
                self.log(f"账号 {account_name} 登录成功，凭证已保存", "auth")
        except Exception as exc:
            with self.lock:
                if self._login_generation == generation:
                    self.login_state.update(
                        {
                            "running": False,
                            "status": "error",
                            "message": str(exc),
                            "qr": "",
                        }
                    )
            self.log(f"扫码登录失败: {exc}", "auth")

    def _wait_with_refresh(self, login: QRLogin, ticket: str, timeout: int, account_name: str) -> dict[str, str]:
        while True:
            if self._login_cancel.is_set():
                raise RuntimeError("登录已取消")
            try:
                return login.wait(ticket, timeout=timeout, cancel=self._qr_refresh, cancel_events=[self._login_cancel])
            except _QrRefreshed:
                if self._login_cancel.is_set():
                    raise RuntimeError("登录已取消")
                self._qr_refresh.clear()
                self.log(f"账号 {account_name} 刷新二维码", "auth")
                url, ticket = login.fetch()
                qr = make_qr_data_uri(url)
                with self.lock:
                    self.login_state.update(
                        {
                            "status": "waiting",
                            "message": "等待扫码确认",
                            "qr": qr,
                            "qr_url": url,
                        }
                    )

    def refresh_login_qr(self) -> None:
        with self.lock:
            if not self.login_state.get("running"):
                raise RuntimeError("当前没有进行中的登录")
            if self.login_state.get("status") != "waiting":
                raise RuntimeError("当前不在等待扫码状态")
        self._qr_refresh.set()

    def cancel_login(self) -> None:
        with self.lock:
            if not self.login_state.get("running"):
                raise RuntimeError("当前没有进行中的登录")
            self.login_state.update(
                {
                    "running": False,
                    "status": "error",
                    "message": "登录流程已取消",
                    "qr": "",
                }
            )
        self._login_cancel.set()
        self.log("登录流程已取消", "auth")


def make_qr_data_uri(text: str) -> str:
    image = qrcode.make(text)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    encoded = base64.b64encode(stream.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def display_account_name(account: dict[str, Any]) -> str:
    return str(account.get("name") or account.get("stuid") or "未命名账号")


def normalize_cn_phone(phone: str) -> str:
    digits = "".join(char for char in str(phone or "") if char.isdigit())
    if len(digits) != 11:
        raise ValueError("请输入 11 位手机号")
    return digits


def find_duplicate_uid(accounts: list[dict[str, Any]], uid: str, exclude_index: int | None = None) -> int | None:
    if not uid:
        return None
    for index, account in enumerate(accounts):
        if exclude_index is not None and index == exclude_index:
            continue
        if str(account.get("stuid") or "").strip() == uid:
            return index
    return None


def preserve_push_channel_secrets(old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
    old_channels = {
        str(channel.get("provider") or ""): channel
        for channel in (old_config.get("push") or {}).get("channels", [])
        if isinstance(channel, dict)
    }
    new_push = new_config.setdefault("push", {})
    new_channels = new_push.setdefault("channels", [])
    if not isinstance(new_channels, list):
        new_push["channels"] = []
        return
    for channel in new_channels:
        if not isinstance(channel, dict):
            continue
        provider = str(channel.get("provider") or "")
        old_channel = old_channels.get(provider)
        if not old_channel:
            continue
        for key, value in old_channel.items():
            if key not in channel or _is_blank_secret(channel.get(key)):
                channel[key] = value


def preserve_web_password(old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
    """前端拿到的配置里没有 web.password（已脱敏），保存时不能把密码冲掉。"""
    old_password = str((old_config.get("web") or {}).get("password") or "")
    new_web = new_config.get("web")
    if not isinstance(new_web, dict):
        new_web = {}
        new_config["web"] = new_web
    if _is_blank_secret(new_web.get("password")):
        new_web["password"] = old_password


def restore_masked_secrets(old_config: dict[str, Any], new_config: dict[str, Any]) -> None:
    """把响应里被掩码的凭证还原成服务端保存的真实值。

    前端是「取回整个 config → 改几个字段 → 整体 POST 回来」的模型，
    所以脱敏必须成对做：响应不回真值，保存时再把真值填回去。
    账号按 stuid 匹配（stuid 不脱敏且唯一），避免删号/改名之后串号。
    """
    old_accounts = [item for item in (old_config.get("accounts") or []) if isinstance(item, dict)]
    old_by_uid = {
        str(item.get("stuid") or ""): item for item in old_accounts if str(item.get("stuid") or "")
    }
    new_accounts = [item for item in (new_config.get("accounts") or []) if isinstance(item, dict)]
    for index, account in enumerate(new_accounts):
        old = old_by_uid.get(str(account.get("stuid") or ""))
        if old is None and len(new_accounts) == len(old_accounts) and index < len(old_accounts):
            # 数量没变说明没有增删账号，按位置回退是安全的（用于改名且还没登录的账号）
            old = old_accounts[index]
        old = old or {}
        for field in MASKED_ACCOUNT_FIELDS:
            if _is_blank_secret(account.get(field)):
                account[field] = str(old.get(field) or "")
        _restore_cloud_tokens(account, old)

    _restore_channels(old_config.get("captcha"), new_config.get("captcha"), MASKED_CAPTCHA_FIELDS)


def _restore_channels(old_section: Any, new_section: Any, fields: tuple[str, ...]) -> None:
    """按 provider 把某个配置段里被掩码的字段还原。"""
    if not isinstance(new_section, dict):
        return
    old_by_provider = {
        str(channel.get("provider") or ""): channel
        for channel in ((old_section or {}).get("channels") or [])
        if isinstance(channel, dict)
    }
    for channel in new_section.get("channels") or []:
        if not isinstance(channel, dict):
            continue
        old_channel = old_by_provider.get(str(channel.get("provider") or "")) or {}
        for field in fields:
            if field in channel and _is_blank_secret(channel.get(field)):
                channel[field] = str(old_channel.get(field) or "")


def _restore_cloud_tokens(account: dict[str, Any], old: dict[str, Any]) -> None:
    tokens = account_cloud_tokens(account)
    if tokens is None:
        return
    old_tokens = account_cloud_tokens(old) or {}
    for key, value in list(tokens.items()):
        if _is_blank_secret(value):
            tokens[key] = str(old_tokens.get(key) or "")


def account_cloud_tokens(account: Any) -> dict[str, Any] | None:
    if not isinstance(account, dict):
        return None
    cloud = account.get("cloud_games")
    if not isinstance(cloud, dict):
        return None
    tokens = cloud.get("tokens")
    return tokens if isinstance(tokens, dict) else None


def _is_blank_secret(value: Any) -> bool:
    """空串 / None / 掩码占位符都表示「客户端没有真值，别覆盖」。"""
    text = str(value if value is not None else "")
    return text == "" or text == MASKED_SECRET


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    """导出给前端的配置：凭证只回「有没有配置」，不回真值。

    前端要靠真值性判断「这个渠道/账号配好了没有」，
    所以用占位符而不是空串——空串会被当成「用户清空了」。
    """
    payload = json.loads(json.dumps(config, ensure_ascii=False))
    web = payload.get("web")
    if isinstance(web, dict):
        web.pop("password", None)
    for account in payload.get("accounts") or []:
        if not isinstance(account, dict):
            continue
        for field in MASKED_ACCOUNT_FIELDS:
            account[field] = MASKED_SECRET if str(account.get(field) or "") else ""
        tokens = account_cloud_tokens(account)
        if tokens is not None:
            for key, value in list(tokens.items()):
                tokens[key] = MASKED_SECRET if str(value or "") else ""
    for channel in ((payload.get("push") or {}).get("channels") or []):
        if isinstance(channel, dict):
            for field in MASKED_PUSH_FIELDS:
                if field in channel:
                    channel[field] = MASKED_SECRET if str(channel.get(field) or "") else ""
    for channel in ((payload.get("captcha") or {}).get("channels") or []):
        if isinstance(channel, dict):
            for field in MASKED_CAPTCHA_FIELDS:
                if field in channel:
                    channel[field] = MASKED_SECRET if str(channel.get(field) or "") else ""
    return payload


def diff_config(old: Any, new: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    if type(old) is not type(new):
        return [(path or "<root>", old, new)]
    if isinstance(old, dict):
        changes: list[tuple[str, Any, Any]] = []
        keys = sorted(set(old) | set(new), key=str)
        for key in keys:
            child_path = f"{path}.{key}" if path else str(key)
            if key not in old:
                changes.extend(diff_added_config(new[key], child_path))
            elif key not in new:
                changes.extend(diff_removed_config(old[key], child_path))
            else:
                changes.extend(diff_config(old[key], new[key], child_path))
        return changes
    if isinstance(old, list):
        changes = []
        common = min(len(old), len(new))
        for index in range(common):
            changes.extend(diff_config(old[index], new[index], f"{path}[{index}]"))
        for index in range(common, len(old)):
            changes.extend(diff_removed_config(old[index], f"{path}[{index}]"))
        for index in range(common, len(new)):
            changes.extend(diff_added_config(new[index], f"{path}[{index}]"))
        return changes
    if old != new:
        return [(path or "<root>", old, new)]
    return []


def diff_added_config(value: Any, path: str) -> list[tuple[str, Any, Any]]:
    if isinstance(value, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(value, key=str):
            changes.extend(diff_added_config(value[key], f"{path}.{key}"))
        return changes
    if isinstance(value, list):
        changes = []
        for index, item in enumerate(value):
            changes.extend(diff_added_config(item, f"{path}[{index}]"))
        return changes
    return [(path, None, value)]


def diff_removed_config(value: Any, path: str) -> list[tuple[str, Any, Any]]:
    if isinstance(value, dict):
        changes: list[tuple[str, Any, Any]] = []
        for key in sorted(value, key=str):
            changes.extend(diff_removed_config(value[key], f"{path}.{key}"))
        return changes
    if isinstance(value, list):
        changes = []
        for index, item in enumerate(value):
            changes.extend(diff_removed_config(item, f"{path}[{index}]"))
        return changes
    return [(path, value, None)]


def format_config_value(path: str, value: Any) -> str:
    if is_sensitive_config_path(path):
        return mask_sensitive_value(value)
    text = json.dumps(redact_config_value(value), ensure_ascii=False, sort_keys=True)
    if len(text) > 120:
        return text[:117] + "..."
    return text


def redact_config_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: mask_sensitive_value(child) if is_sensitive_config_path(str(key)) else redact_config_value(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact_config_value(item) for item in value]
    return value


def mask_sensitive_value(value: Any) -> str:
    return "<空>" if value in (None, "") else "<已设置>"


def should_log_config_change(path: str, old_value: Any, new_value: Any) -> bool:
    if format_config_value(path, old_value) == format_config_value(path, new_value):
        return False
    if old_value is None and new_value in ("", [], {}, None):
        return False
    if old_value is None and path.startswith("push.channels[") and path.rsplit(".", 1)[-1] in {"smtp_port", "smtp_ssl"}:
        return False
    return True


def is_sensitive_config_path(path: str) -> bool:
    sensitive_names = {
        "cookie",
        "stoken",
        "mid",
        "token",
        "tokens",
        "webhook",
        "userkey",
        "secret",
        "password",
        "smtp_password",
        "fp",
    }
    parts = [part.split("[", 1)[0].lower() for part in path.replace("]", "").split(".")]
    return any(part in sensitive_names or part.endswith("_token") for part in parts)


def first_query(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    return values[0]


class Handler(BaseHTTPRequestHandler):
    app: WebApp

    def log_message(self, format: str, *args: object) -> None:
        return

    def _get_cookie(self, name: str) -> str:
        cookie_header = self.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            part = part.strip()
            if part.startswith(f"{name}="):
                return part[len(name) + 1:]
        return ""

    def _is_authenticated(self) -> bool:
        if not self.app.need_auth:
            return True
        token = self._get_cookie(AUTH_COOKIE)
        return self.app._check_session(token)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)
        try:
            if path == "/api/auth/status":
                self.send_json(self.app.auth_status())
                return
            if not self._is_authenticated() and path.startswith("/api/"):
                self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
                return
            if path == "/api/config":
                self.send_json(self.app.get_config())
                return
            if path == "/api/status":
                self.send_json(self.app.status())
                return
            if path == "/api/shop/goods":
                self.send_json(self.app.shop_goods(str(first_query(query, "game"))))
                return
            if path == "/api/shop/good-detail":
                self.send_json(self.app.shop_good_detail(str(first_query(query, "goods_id"))))
                return
            if path == "/api/shop/account-meta":
                account_index = int(first_query(query, "account_index", "0") or 0)
                game_biz = str(first_query(query, "game_biz", ""))
                self.send_json(self.app.shop_account_meta(account_index, game_biz))
                return
            if path == "/api/shop/device-fp":
                self.send_json(self.app.ensure_shop_device_fp())
                return
            self.serve_static(path)
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self.read_json()
            if path == "/api/auth/setup":
                token = self.app.auth_setup(str(payload.get("password", "")))
                self.send_json({"ok": True}, set_cookie=token)
                return
            if path == "/api/auth/login":
                token = self.app.auth_login(str(payload.get("password", "")))
                self.send_json({"ok": True}, set_cookie=token)
                return
            if not self._is_authenticated():
                self.send_error_json("未登录", HTTPStatus.UNAUTHORIZED)
                return
            if path == "/api/config":
                self.app.set_config(payload)
                self.send_json({"ok": True})
                return
            if path == "/api/device/reroll":
                device = self.app.reroll_device(str(payload.get("preset") or ""))
                self.send_json({"ok": True, "device": device})
                return
            if path == "/api/push/test":
                result = self.app.test_push_channels()
                self.send_json({"ok": True, "result": result})
                return
            if path == "/api/ip/check":
                self.send_json({"ok": True, **self.app.check_ip()})
                return
            if path == "/api/captcha/check":
                self.send_json({"ok": True, **self.app.check_captcha_env()})
                return
            if path == "/api/run":
                self.app.log("收到手动执行请求", "web")
                started = self.app.scheduler.run_now()
                if not started:
                    self.app.log("手动执行请求被拒绝：任务正在运行", "scheduler")
                    self.send_error_json("任务正在运行", HTTPStatus.CONFLICT)
                    return
                self.send_json({"ok": True})
                return
            if path == "/api/run/stop":
                stopped = self.app.stop_run()
                if not stopped:
                    self.send_error_json("当前没有正在运行的任务", HTTPStatus.CONFLICT)
                    return
                self.send_json({"ok": True})
                return
            if path == "/api/login/start":
                account_index = int(payload.get("account_index", -1))
                timeout = int(payload.get("timeout") or 120)
                account_payload = payload.get("account")
                if account_payload is not None and not isinstance(account_payload, dict):
                    raise ValueError("账号数据必须是 JSON 对象")
                self.app.start_login(account_index, timeout, account_payload, bool(payload.get("draft")))
                self.send_json({"ok": True})
                return
            if path == "/api/login/captcha/send":
                account_index = int(payload.get("account_index", -1))
                account_payload = payload.get("account")
                if account_payload is not None and not isinstance(account_payload, dict):
                    raise ValueError("账号数据必须是 JSON 对象")
                result = self.app.send_login_captcha(
                    account_index,
                    str(payload.get("phone") or ""),
                    account_payload,
                    bool(payload.get("draft")),
                    str(payload.get("aigis") or ""),
                )
                self.send_json({"ok": True, **result})
                return
            if path == "/api/login/captcha/verify":
                account_index = int(payload.get("account_index", -1))
                account_payload = payload.get("account")
                if account_payload is not None and not isinstance(account_payload, dict):
                    raise ValueError("账号数据必须是 JSON 对象")
                result = self.app.complete_login_captcha(
                    account_index,
                    str(payload.get("phone") or ""),
                    str(payload.get("captcha") or ""),
                    str(payload.get("action_type") or ""),
                    account_payload,
                    bool(payload.get("draft")),
                    str(payload.get("aigis") or ""),
                )
                self.send_json({"ok": True, **result})
                return
            if path == "/api/login/refresh":
                self.app.refresh_login_qr()
                self.send_json({"ok": True})
                return
            if path == "/api/login/cancel":
                self.app.cancel_login()
                self.send_json({"ok": True})
                return
            if path == "/api/shop/exchange":
                if "plan_index" in payload:
                    result = self.app.shop_exchange_plan_once(int(payload.get("plan_index")))
                    self.send_json({"ok": True, "result": result, "config": self.app.get_config()})
                    return
                plan = payload.get("plan")
                if not isinstance(plan, dict):
                    raise ValueError("兑换计划必须是 JSON 对象")
                result = self.app.shop_exchange_once(plan)
                # 处理直接传递plan的情况，也需要推送支持
                shop_config = self.app.config.get("shop_exchange", {})
                if shop_config.get("push", False):
                    goods_name = plan.get("goods_name") or plan.get("goods_id") or "未知商品"
                    self.app._send_exchange_push(goods_name, result, plan)
                self.send_json({"ok": True, "result": result})
                return
            self.send_error_json("接口不存在", HTTPStatus.NOT_FOUND)
        except AigisRequired as exc:
            self.send_json(
                {
                    "ok": False,
                    "error": str(exc),
                    "code": "aigis_required",
                    "aigis": exc.aigis,
                },
                HTTPStatus.PRECONDITION_REQUIRED,
            )
        except Exception as exc:
            self.send_error_json(str(exc), HTTPStatus.BAD_REQUEST)

    def serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        relative = pathlib.Path(unquote(path).lstrip("/"))
        # 移除版本参数（如 /app.js?v=123 -> /app.js）
        if len(relative.parts) > 0:
            file_part = relative.parts[-1]
            if "?" in file_part:
                file_part = file_part.split("?")[0]
                relative = pathlib.Path(*relative.parts[:-1], file_part)

        target = (WEB_ROOT / relative).resolve()
        root = WEB_ROOT.resolve()
        if root not in target.parents and target != root:
            self.send_error_json("路径非法", HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error_json("文件不存在", HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()

        # 如果是 HTML 文件，给静态资源加版本号，改了前端刷新就能生效
        if content_type == "text/html":
            html_content = data.decode("utf-8")
            for asset in ("app.js", "app.css"):
                asset_path = WEB_ROOT / asset
                if not asset_path.exists():
                    continue
                mtime = int(asset_path.stat().st_mtime)
                html_content = html_content.replace(
                    f'src="/{asset}"', f'src="/{asset}?v={mtime}"'
                ).replace(
                    f'href="/{asset}"', f'href="/{asset}?v={mtime}"'
                )
            data = html_content.encode("utf-8")

        # 生成 ETag（基于文件内容和修改时间）
        mtime = target.stat().st_mtime
        size = target.stat().st_size
        etag = f'"{int(mtime)}-{size}"'

        # 检查 If-None-Match 头
        if self.headers.get("If-None-Match") == etag:
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.end_headers()
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("ETag", etag)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("请求体必须是 JSON 对象")
        return data

    def send_json(self, data: dict[str, Any], status: HTTPStatus = HTTPStatus.OK, set_cookie: str = "") -> None:
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        if set_cookie:
            # 7 天过期 (7 * 24 * 60 * 60 = 604800 秒)
            self.send_header("Set-Cookie", f"{AUTH_COOKIE}={set_cookie}; Path=/; HttpOnly; SameSite=Strict; Max-Age=604800")
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, message: str, status: HTTPStatus) -> None:
        self.send_json({"ok": False, "error": message}, status=status)


def serve(config_path: pathlib.Path, host: str, port: int) -> None:
    app = WebApp(config_path, bound_host=host)
    Handler.app = app
    print_startup_banner("MYQ")
    app.log(f"正在启动 Web 控制台，配置文件: {config_path.resolve()}", "startup")
    if app.need_auth:
        app.log(f"外网模式（监听 {host}），访问需要密码", "auth")
        if not app.password_is_set:
            app.log("尚未设置访问密码，请打开控制台后按提示设置", "auth")
    server, actual_port = create_server(host, port)
    if actual_port != port:
        app.log(f"端口 {port} 被占用，已切换到 {actual_port}", "startup")
    app.start()

    # 显示可访问的地址
    if host == "0.0.0.0":
        # 外网模式，显示本地访问地址
        local_url = f"http://127.0.0.1:{actual_port}"
        app.log(f"Web 控制台已启动: {local_url}", "web")
        app.log("外网访问请替换为实际 IP 地址", "web")
    else:
        url = f"http://{host}:{actual_port}"
        app.log(f"Web 控制台已启动: {url}", "web")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        app.log("收到退出信号，正在停止 Web 控制台", "startup")
    finally:
        app.stop()
        server.server_close()
        app.log("Web 控制台已停止", "startup")


def _port_candidates(port: int) -> Iterator[int]:
    """按优先级给出候选端口。

    系统保留端口（Windows 开了 Hyper-V / WSL 时很常见）会让**连续一整块**端口都
    无法监听，所以不能只按 +1 往后试：2026-09-26 就是这样，5890~5919 整段落在保留区里，
    30 次尝试全部失败，控制台停了一整天。这里先按 1000 的步长往外跳，再退回逐个试。
    """
    yield port
    for step in range(1, 21):
        high = port + step * 1000
        if high <= 65535:
            yield high
        low = port - step * 1000
        if low >= 1024:
            yield low
    for delta in range(1, 30):
        if port + delta <= 65535:
            yield port + delta


def create_server(host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    last_error: OSError | None = None
    tried: list[int] = []
    for candidate in _port_candidates(port):
        tried.append(candidate)
        try:
            return ThreadingHTTPServer((host, candidate), Handler), candidate
        except OSError as exc:
            last_error = exc
            # 10013 = 端口被系统保留，10048 = 已被占用；其它错误直接抛
            if getattr(exc, "winerror", None) not in (10013, 10048):
                raise

    hint = ""
    if getattr(last_error, "winerror", None) == 10013:
        hint = (
            "。注意 10013 不是被别的程序占用，而是端口被系统保留 —— Windows 开启 "
            "Hyper-V / WSL 后会保留成块的端口，而且每次重启保留的区间都可能变。"
            "用 netsh int ipv4 show excludedportrange protocol=tcp 查看保留区间，"
            "再把 config.yaml 的 web.port 换成一个不在保留区间内的端口"
            "（选在动态端口范围之外的端口最稳妥）"
        )
    raise OSError(f"尝试了 {len(tried)} 个端口（{tried[0]} 起）都无法监听: {last_error}{hint}")
