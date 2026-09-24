# -*- coding: utf-8 -*-
"""任务执行编排。"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable

from ..core import captcha
from ..core.config import (
    MAX_ACCOUNT_GAP_MINUTES,
    account_config,
    account_has_tasks,
    find_account,
    save_config,
)
from ..core.http import ApiClient
from ..tasks.bbs import BbsTasks
from ..tasks.cloud_games import CloudGameCheckin
from ..tasks.games import GameCheckin
from .ip_guard import ensure_mainland_ip, format_duration
from .notifier import ACCOUNT_CRASH_MARKER, build_push_title, is_task_success, send_push

EmitFn = Callable[[str], None]
ComponentEmitFn = Callable[[str, str], None]

# 账号之间的防风控等待（秒）：模拟多个真人在不同时段操作，
# 降低「同 IP / 同设备指纹短时间内多账号」被风控的概率。
#
# 实际取值以配置段 account_gap（分钟，可在网页「每日调度」里改）为准；
# 这个常量只作为该配置段缺失/不可用时的兜底，默认等于旧版本的行为（1~2 小时）。
ACCOUNT_GAP_RANGE = (3600, 7200)


def account_gap_seconds(config: dict[str, Any]) -> float:
    """本次「跑下一个账号」之前要等多少秒。0 表示不等待。

    配置里是分钟（网页上也是分钟）。范围取 [min, max] 内的随机值，
    这样多账号看起来像不同时段各自操作的真人。
    """
    gap = config.get("account_gap")
    if not isinstance(gap, dict):
        return random.uniform(*ACCOUNT_GAP_RANGE)
    if not gap.get("enable", True):
        return 0.0
    low = _gap_minutes(gap.get("min_minutes"), 60)
    high = _gap_minutes(gap.get("max_minutes"), 120)
    if high < low:
        low, high = high, low
    if high <= 0:
        return 0.0
    return random.uniform(low * 60, high * 60)


def _gap_minutes(value: Any, default: int) -> int:
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(minutes, 0), MAX_ACCOUNT_GAP_MINUTES)


def interruptible_sleep(seconds: float, stop_event: threading.Event | None) -> bool:
    """等待指定秒数；期间收到中断信号就提前返回 False。"""
    if stop_event is None:
        time.sleep(seconds)
        return True
    return not stop_event.wait(timeout=seconds)


def run_tasks(
    config: dict[str, Any],
    config_path: str,
    account_name: str | None = None,
    games_only: bool = False,
    bbs_only: bool = False,
    only_games: list[str] | None = None,
    emit: EmitFn | None = None,
    emit_component: ComponentEmitFn | None = None,
    stop_event: threading.Event | None = None,
) -> list[str]:
    """跑一轮签到任务。

    结束后（无论成功、报错还是被中断）都会释放常驻的视觉识别模型，
    把约 139MB 内存还给系统；下次遇到验证码时会自动重新加载（约 0.5 秒）。
    """
    try:
        return _run_tasks(
            config,
            config_path,
            account_name,
            games_only,
            bbs_only,
            only_games,
            emit,
            emit_component,
            stop_event,
        )
    finally:
        captcha.release_matcher()


def _run_tasks(
    config: dict[str, Any],
    config_path: str,
    account_name: str | None = None,
    games_only: bool = False,
    bbs_only: bool = False,
    only_games: list[str] | None = None,
    emit: EmitFn | None = None,
    emit_component: ComponentEmitFn | None = None,
    stop_event: threading.Event | None = None,
) -> list[str]:
    output: list[str] = []

    def add(message: str, component: str = "task") -> None:
        output.append(message)
        if emit_component:
            emit_component(message, component)
        elif emit:
            emit(message)

    def stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    if games_only and bbs_only:
        raise ValueError("games_only 和 bbs_only 不能同时启用")
    if not config.get("enable", True):
        add("配置 enable=false，已跳过。")
        return output
    accounts = [find_account(config, account_name)] if account_name else list(config.get("accounts", []))
    if not accounts:
        add("没有配置账号，已跳过。")
        return output
    should_emit = bool(emit or emit_component)
    account_push = bool((config.get("push") or {}).get("per_account", False))
    try:
        for index, account in enumerate(accounts, start=1):
            if stopped():
                add("# ⏹ 已停止，剩余账号不再执行")
                break
            # 账号间加随机间隔，降低多账号被风控的概率；等待期间可以随时停止。
            # 具体等多久由配置段 account_gap 决定（网页「每日调度」里可改，0 = 不等）。
            if index > 1:
                account_gap = account_gap_seconds(config)
                if account_gap <= 0:
                    add("# 账号间隔设为 0，直接跑下一个账号")
                else:
                    gap_config = config.get("account_gap") or {}
                    add(
                        f"# ⏳ 账号间防风控等待 {format_duration(account_gap)}"
                        f"（配置 {gap_config.get('min_minutes', 60)}~{gap_config.get('max_minutes', 120)} 分钟），"
                        "再跑下一个账号"
                    )
                    if not interruptible_sleep(account_gap, stop_event):
                        add("# ⏹ 等待已被手动停止，剩余账号不再执行")
                        break
            # 出口 IP 守卫：VPN / 代理把流量引到境外时暂停签到，避免异地登录触发风控
            if not ensure_mainland_ip(config, add, stop_event=stop_event):
                if stopped():
                    add("# ⏹ 已停止，剩余账号不再执行")
                    break
                add(f"# 跳过账号 {account.get('name', '未命名')}：出口 IP 未恢复")
                continue
            add(f"# 账号 {index}/{len(accounts)}: {account.get('name', '未命名')}")
            start = len(output)
            try:
                # 账号可以拥有独立的任务配置，未单独配置时跟随全局配置。
                account_tasks = account_config(config, account)
                features = account_tasks.get("features", {})
                if account_has_tasks(account):
                    add("# 该账号使用独立任务配置")
                with ApiClient() as client:
                    if not bbs_only and features.get("game_checkin", True):
                        lines = GameCheckin(
                            client,
                            account_tasks,
                            account,
                            emit=(lambda message: add(message, "game")) if should_emit else None,
                        ).run(only_games=only_games)
                        if not should_emit:
                            output.extend(lines)
                    if not stopped() and not bbs_only and features.get("cloud_game_checkin", False):
                        lines = CloudGameCheckin(
                            client,
                            account_tasks,
                            account,
                            emit=(lambda message: add(message, "cloud")) if should_emit else None,
                        ).run()
                        if not should_emit:
                            output.extend(lines)
                    if not stopped() and not games_only and features.get("bbs_tasks", False):
                        lines = BbsTasks(
                            client,
                            account_tasks,
                            account,
                            emit=(lambda message: add(message, "bbs")) if should_emit else None,
                        ).run()
                        if not should_emit:
                            output.extend(lines)
            except Exception as exc:
                # 单个账号的接口/网络异常（超时、5xx、429…）不能吃掉当天剩下的账号；
                # 记成失败让推送说实话，然后继续跑下一个账号。
                add(f"{ACCOUNT_CRASH_MARKER}（{account.get('name', '未命名')}）：{exc}")
            if account_push:
                _push_account_result(config, add, account, output[start:])
            if stopped():
                add("# ⏹ 已停止，剩余账号不再执行")
                break
    finally:
        # 必须落盘：login 刷新出来的 cookie_token 存在内存配置里，
        # 异常路径跳过这一步就等于把刚续上的登录态丢了。
        save_config(config_path, config)
    return output


def _push_account_result(
    config: dict[str, Any],
    add: EmitFn,
    account: dict[str, Any],
    account_lines: list[str],
) -> None:
    """每跑完一个账号就推一次，不用等全部账号跑完。"""
    name = str(account.get("name") or "未命名")
    # 借用整体解析逻辑，账号行/失败项的呈现与总结推送保持一致
    lines = [f"# 账号 1/1: {name}", *account_lines]
    success = is_task_success(lines)
    title = f"【{name}】" + build_push_title(lines, success).removeprefix("米游签 · ")
    result = send_push(config, title, "\n".join(lines), success=success)
    if result:
        add(f"# 📨 已推送「{name}」结果：{result}")
