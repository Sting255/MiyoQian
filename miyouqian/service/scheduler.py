# -*- coding: utf-8 -*-
"""每日随机波动调度器。"""

from __future__ import annotations

import random
import threading
from datetime import datetime, timedelta
from typing import Any, Callable


LogFn = Callable[[str], None]
RunFn = Callable[[threading.Event], list[str]]


class DailyScheduler:
    def __init__(self, config: dict[str, Any], run_fn: RunFn, log_fn: LogFn) -> None:
        self.config = config
        self.run_fn = run_fn
        self.log = log_fn
        self._stop = threading.Event()
        self._wake = threading.Event()
        # 传给 run_fn 的中断信号：账号间等待、IP 等待、任务之间都会检查它
        self._stop_run = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._running = False
        self._next_run: datetime | None = None
        self._last_run: datetime | None = None
        self._last_error = ""
        self._schedule_signature = self._make_schedule_signature(self._schedule_config())
        self._log_next_run_on_recompute = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="miyouqian-scheduler", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def reload(self, config: dict[str, Any]) -> None:
        old_signature = self._schedule_signature
        self.config = config
        new_signature = self._make_schedule_signature(self._schedule_config())
        with self._lock:
            self._schedule_signature = new_signature
            if new_signature != old_signature:
                self._next_run = None
                self._log_next_run_on_recompute = new_signature[1] != old_signature[1]
        self._wake.set()

    def run_now(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._stop_run.clear()
        threading.Thread(target=self._run_once, name="miyouqian-manual-run", daemon=True).start()
        return True

    def stop_run(self) -> bool:
        """请求中断正在执行的任务。返回 False 表示当前没有任务在跑。"""
        with self._lock:
            if not self._running:
                return False
        self._stop_run.set()
        return True

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": bool(self._schedule_config().get("enable", True)),
                "running": self._running,
                "stopping": self._stop_run.is_set() and self._running,
                "next_run": self._next_run.isoformat(timespec="seconds") if self._next_run else "",
                "last_run": self._last_run.isoformat(timespec="seconds") if self._last_run else "",
                "last_error": self._last_error,
                "schedule": self._schedule_config(),
            }

    def _loop(self) -> None:
        if self._schedule_config().get("run_on_start", False):
            self.run_now()
        while not self._stop.is_set():
            try:
                self._loop_once()
            except Exception as exc:
                # 配置写错（比如 schedule.time 不是 HH:MM）绝不能把调度线程弄死：
                # 线程一死自动签到就永远不再触发，而网页上还显示着「已启用」。
                # 这里记进 last_error（网页会显示）并 30 秒后重试。
                with self._lock:
                    self._last_error = f"调度异常: {exc}"
                self.log(f"调度异常，30 秒后重试: {exc}")
                self._stop.wait(timeout=30)

    def _loop_once(self) -> None:
        notice = ""
        schedule = self._schedule_config()
        if not schedule.get("enable", True):
            with self._lock:
                self._next_run = None
            self._wake.wait(timeout=30)
            self._wake.clear()
            return
        with self._lock:
            if self._next_run is None or self._next_run <= datetime.now() - timedelta(minutes=5):
                log_change = self._log_next_run_on_recompute
                self._log_next_run_on_recompute = False
                self._next_run = self._compute_next_base_run(schedule, log_change=log_change)
            next_run = self._next_run
        wait_seconds = max((next_run - datetime.now()).total_seconds(), 0)
        if self._wake.wait(timeout=min(wait_seconds, 60)):
            self._wake.clear()
            return
        if datetime.now() < next_run:
            return
        with self._lock:
            if self._running:
                notice = self._defer_to_tomorrow(schedule, "上一次任务还在跑")
            else:
                due_run = self._next_run
        if notice:
            self.log(notice)
            return
        if not self._wait_jitter(schedule, due_run):
            return
        # 随机延后窗口最长可达 jitter_minutes（默认 45 分钟）：期间用户完全可能
        # 关掉自动执行或改了时间，所以这里必须重新确认一次再跑。
        if not self._schedule_config().get("enable", True):
            with self._lock:
                self._next_run = None
            self.log("自动执行已在随机延后期间被关闭，本次不再执行")
            return
        with self._lock:
            if self._running:
                notice = self._defer_to_tomorrow(schedule, "上一次任务还在跑")
            elif self._last_run and due_run and self._last_run >= due_run:
                notice = self._defer_to_tomorrow(schedule, "本次已经在手动执行里跑过了")
            else:
                self._running = True
        if notice:
            self.log(notice)
            return
        self._run_once()
        with self._lock:
            self._next_run = self._compute_next_base_run(self._schedule_config(), tomorrow=True, log_change=False)

    def _defer_to_tomorrow(self, schedule: dict[str, Any], reason: str) -> str:
        """把当天的自动执行顺延到明天，返回要写进日志的一句话。

        必须在持锁时调用，并且**不要在这里打日志**：`WebApp.set_config()` 是
        「持自己的锁 → 调 scheduler.reload() 拿调度锁」，调度器这边如果反着来
        （持调度锁 → 调 self.log 拿 WebApp 锁）就会 ABBA 死锁。
        """
        self._next_run = self._compute_next_base_run(schedule, tomorrow=True, log_change=False)
        target = self._next_run.strftime("%Y-%m-%d %H:%M:%S") if self._next_run else "未知"
        return f"今天的自动执行已跳过（{reason}），下次: {target}"

    def _run_once(self) -> None:
        self.log("开始执行签到任务")
        try:
            for line in self.run_fn(self._stop_run):
                self.log(line)
            with self._lock:
                self._last_run = datetime.now()
                self._last_error = ""
            if self._stop_run.is_set():
                self.log("签到任务已手动停止")
            else:
                self.log("签到任务执行完成")
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self.log(f"签到任务失败: {exc}")
        finally:
            with self._lock:
                self._running = False
            self._stop_run.clear()

    def _schedule_config(self) -> dict[str, Any]:
        schedule = self.config.get("schedule", {})
        return schedule if isinstance(schedule, dict) else {}

    def _compute_next_base_run(
        self,
        schedule: dict[str, Any],
        tomorrow: bool = False,
        log_change: bool = False,
    ) -> datetime:
        hour, minute = parse_time(str(schedule.get("time", "09:00")))
        base_day = datetime.now().date()
        if tomorrow:
            base_day = base_day + timedelta(days=1)
        target = datetime.combine(base_day, datetime.min.time()).replace(hour=hour, minute=minute)
        if target <= datetime.now():
            return self._compute_next_base_run(schedule, tomorrow=True, log_change=log_change)
        if log_change:
            self.log(f"下次自动执行时间: {target.strftime('%Y-%m-%d %H:%M:%S')}")
        return target

    def _wait_jitter(self, schedule: dict[str, Any], due_run: datetime | None) -> bool:
        jitter = max(int(schedule.get("jitter_minutes", 45) or 0), 0)
        if not jitter:
            return True
        delay_seconds = random.randint(0, jitter * 60)
        if delay_seconds <= 0:
            return True
        if due_run:
            delay_min_str = f"{delay_seconds // 60}分{delay_seconds % 60}秒" if delay_seconds >= 60 else f"{delay_seconds}秒"
            self.log(f"已到自动执行时间 {due_run.strftime('%Y-%m-%d %H:%M:%S')}，随机延后 {delay_min_str} 后执行")
        with self._lock:
            self._next_run = datetime.now() + timedelta(seconds=delay_seconds)
        if self._stop.wait(timeout=delay_seconds):
            return False
        return True

    def _make_schedule_signature(self, schedule: dict[str, Any]) -> tuple[Any, ...]:
        return (
            bool(schedule.get("enable", True)),
            str(schedule.get("time", "09:00")),
        )


def parse_time(value: str) -> tuple[int, int]:
    parts = value.strip().split(":", 1)
    if len(parts) != 2:
        raise ValueError("schedule.time 必须是 HH:MM 格式")
    hour = int(parts[0])
    minute = int(parts[1])
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("schedule.time 超出范围")
    return hour, minute
