# -*- coding: utf-8 -*-
"""签到前的出口 IP 守卫。

检测到公网出口 IP 不在中国大陆时暂停签到，等 IP 回到大陆再继续；
等待超过上限仍未恢复就放弃本次，并按配置推送通知。

同时探测国内直连链路与境外链路：分流模式（规则模式）的代理下，
米游社作为境外服务走的是代理那条路，只看国内直连会漏判。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from ..core.ipcheck import LinkReport, probe_links
from .notifier import send_push

EmitFn = Callable[[str], None]


def format_duration(seconds: int) -> str:
    seconds = max(int(seconds or 0), 0)
    if seconds < 3600:
        return f"{max(seconds // 60, 1)} 分钟"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} 小时" + (f" {minutes} 分钟" if minutes else "")


def ensure_mainland_ip(
    config: dict[str, Any],
    add: EmitFn,
    *,
    stop_event: threading.Event | None = None,
    sleep: Callable[[float], None] = time.sleep,
    probe_fn: Callable[..., LinkReport] | None = None,
) -> bool:
    """确认出口 IP 在中国大陆。返回 True 表示可以继续签到。"""
    # 运行时再取 probe，便于测试替换
    probe_fn = probe_fn or probe_links
    guard = config.get("ip_guard") or {}
    if not guard.get("enable"):
        return True
    interval = max(int(guard.get("check_interval") or 300), 30)
    max_wait = max(int(guard.get("max_wait") or 0), 0)
    notify = bool(guard.get("notify", True))
    block_on_error = str(guard.get("on_error") or "allow").lower() == "block"
    endpoints = guard.get("endpoints") or []

    waited = 0
    paused = False
    while True:
        if stop_event is not None and stop_event.is_set():
            add("# ⏹ IP 等待已被手动停止")
            return False
        report = probe_fn(domestic=endpoints or None)
        verdict = report.mainland

        if verdict is True:
            if paused:
                add(f"# ✅ 出口 IP 已回到中国大陆（{report.domestic.label}），继续签到")
                _notify(
                    config,
                    notify,
                    "米游签 · 出口 IP 已恢复，继续签到",
                    f"当前出口：{report.domestic.label}",
                    True,
                )
            else:
                add(f"# 🌐 出口 IP 检测通过：{report.domestic.label}（中国大陆）")
            return True

        if verdict is False:
            reason = report.reason
            detail = reason
            if report.proxy:
                detail += f"\n系统代理：{report.proxy}"
        else:
            if report.error:
                reason = f"IP 查询失败：{report.error}"
            else:
                reason = "出口 IP 属地无法判断"
            if not block_on_error:
                add(f"# ⚠️ {reason}，按配置放行本次签到")
                return True
            detail = reason

        if not paused:
            paused = True
            add(f"# 🚫 检测到境外出口（{reason}），已暂停签到")
            wait_hint = f"每 {format_duration(interval)}复查一次"
            wait_hint += f"，最多等 {format_duration(max_wait)}" if max_wait else "，不限时长"
            add(f"# ⏳ {wait_hint}；关掉 VPN 后会自动继续")
            _notify(config, notify, "米游签 · 暂停签到（出口 IP 不在中国大陆）", detail, False)

        if max_wait and waited >= max_wait:
            add(f"# ⌛ 已等待 {format_duration(max_wait)}，出口 IP 仍未恢复，本次签到已放弃")
            _notify(config, notify, "米游签 · 本次签到已放弃（出口 IP 仍未恢复）", detail, False)
            return False

        step = min(interval, max_wait - waited) if max_wait else interval
        add(f"# ⏳ 等待 {format_duration(step)}后再次检测出口 IP")
        if stop_event is not None:
            if stop_event.wait(timeout=step):
                add("# ⏹ IP 等待已被手动停止")
                return False
        else:
            sleep(step)
        waited += step


def mainland_ok_now(
    config: dict[str, Any],
    probe_fn: Callable[..., LinkReport] | None = None,
) -> bool | None:
    """**非阻塞**地查一次出口 IP：True=大陆 / False=境外 / None=判断不出或守卫没开。

    抢购场景用这个而不是 `ensure_mainland_ip`：后者会一直等到 IP 恢复，
    而抢购是「推迟几秒就错过」的场景，只能二选一——要么现在发，要么这次不发。
    """
    probe_fn = probe_fn or probe_links
    guard = config.get("ip_guard") or {}
    if not guard.get("enable"):
        return None
    endpoints = guard.get("endpoints") or []
    try:
        report = probe_fn(domestic=endpoints or None)
    except Exception:
        return None
    verdict = report.mainland
    if verdict is None and str(guard.get("on_error") or "allow").lower() == "block":
        return False
    return verdict


def _notify(
    config: dict[str, Any],
    notify: bool,
    title: str,
    message: str,
    success: bool,
) -> None:
    if not notify:
        return
    try:
        send_push(config, title, message, success=success)
    except Exception:
        # 推送失败不能影响签到流程
        pass
