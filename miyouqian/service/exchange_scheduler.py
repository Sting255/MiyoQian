# -*- coding: utf-8 -*-
"""商品兑换计划调度器。"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx

from .. import constants as c
from .ip_guard import mainland_ok_now

LogFn = Callable[[str], None]
RunPlanFn = Callable[[int], None]

PRECISE_WAIT_SECONDS = 180
CHECK_INTERVAL_SECONDS = 0.1


class ExchangeScheduler:
    def __init__(self, config: dict[str, Any], run_plan_fn: RunPlanFn, log_fn: LogFn) -> None:
        self.config = config
        self.run_plan_fn = run_plan_fn
        self.log = log_fn
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers: dict[str, PlanWorker] = {}
        self._running_plans: set[int] = set()
        self._progress: dict[int, dict[str, Any]] = {}
        self._last_error = ""

    def start(self) -> None:
        self._stop.clear()
        self._rebuild_workers()

    def stop(self) -> None:
        self._stop.set()
        self._stop_workers()

    def reload(self, config: dict[str, Any]) -> None:
        self.config = config
        if not self._stop.is_set():
            self._rebuild_workers()

    def status(self) -> dict[str, Any]:
        with self._lock:
            running_plans = sorted(self._running_plans)
            running_progress = {str(i): dict(v) for i, v in self._progress.items()}
            last_error = self._last_error
            worker_count = len(self._workers)
        next_plan = self._next_plan()
        return {
            "enabled": bool(self._shop_config().get("enable", False)),
            "running_plans": running_plans,
            "running_count": len(running_plans),
            "running_progress": running_progress,
            "worker_count": worker_count,
            "next_run": format_ts(next_plan[1]) if next_plan else "",
            "next_plan": next_plan[0] if next_plan else None,
            "last_error": last_error,
        }

    def update_progress(self, index: int, *, attempt: int | None = None, message: str | None) -> None:
        """更新某条兑换计划的实时进度（并发安全，供 Web 层在重试过程中回调）。"""
        with self._lock:
            entry = self._progress.setdefault(index, self._initial_progress(index))
            if attempt is not None:
                entry["attempt"] = attempt
            if message is not None:
                entry["message"] = message

    def clear_progress(self, index: int) -> None:
        """清除某条计划的实时进度，使前端回落到最终结果展示。"""
        with self._lock:
            self._progress.pop(index, None)

    def _initial_progress(self, index: int) -> dict[str, Any]:
        goods_name = f"计划 {index + 1}"
        plans = self._shop_config().get("plans") or []
        if 0 <= index < len(plans):
            plan = plans[index]
            if isinstance(plan, dict):
                goods_name = str(plan.get("goods_name") or plan.get("goods_id") or goods_name)
        return {
            "goods_name": goods_name,
            "attempt": 0,
            "message": "准备兑换",
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }

    def _rebuild_workers(self) -> None:
        if self._stop.is_set():
            return
        shop = self._shop_config()
        # 同一个账号 + 同一商品 + 同一开抢时间的多条计划才会互相覆盖（worker 键相同），
        # 只会执行第一条。注意「不同账号」的同商品同时间计划是正常的，各跑各的。
        for key in self.duplicate_worker_keys(shop.get("plans") or []):
            self.log(
                f"⚠️ 兑换计划冲突：同一账号有多个计划的商品 + 开抢时间完全相同（{key}），"
                "只会执行排在最前面的那条，请把时间错开"
            )
        desired: dict[str, tuple[int, dict, int]] = {}
        if shop.get("enable", False):
            now = time.time()
            for index, plan in enumerate(shop.get("plans") or []):
                if not isinstance(plan, dict):
                    continue
                exchange_at = parse_ts(plan.get("exchange_at"))
                if not plan.get("enable", True) or not plan.get("auto", True) or exchange_at <= 0:
                    continue
                attempt_key = self._attempt_key(plan, exchange_at)
                if str(plan.get("last_attempt_key") or "") == attempt_key:
                    continue
                if exchange_at <= now:
                    continue
                desired[self._worker_key(plan, exchange_at)] = (index, plan, exchange_at)

        with self._lock:
            current_keys = set(self._workers.keys())
            desired_keys = set(desired.keys())
            to_remove = current_keys - desired_keys
            to_add = desired_keys - current_keys

            removed_workers = [self._workers.pop(k) for k in to_remove]
            new_workers: dict[str, PlanWorker] = {}
            # 保留下来的线程要同步最新的下标和配置快照。用户在网页上删掉/重排计划后，
            # 线程若还按旧下标执行，就会去兑换**另一个计划**（错的商品/账号/地址），
            # 所以这里必须原地更新，而不是让它带着旧下标继续等。
            for key in current_keys & desired_keys:
                index, plan, _exchange_at = desired[key]
                self._workers[key].update(index, plan)
            for key in to_add:
                index, plan, exchange_at = desired[key]
                worker = PlanWorker(index, plan, exchange_at, key, self._run_worker_plan, self.log,
                                    ip_check=self._ip_check)
                self._workers[key] = worker
                new_workers[key] = worker


        current_thread = threading.current_thread()
        for worker in removed_workers:
            worker.stop()
        for worker in removed_workers:
            if worker.is_current_thread(current_thread):
                continue
            worker.join(timeout=1)

        for worker in new_workers.values():
            worker.start()
        if new_workers:
            self.log(f"已启动 {len(new_workers)} 个商品兑换计划线程")

    def _stop_workers(self) -> None:
        """方法现仅用于整体 stop()"""
        with self._lock:
            workers = list(self._workers.values())
            self._workers = {}
        current_thread = threading.current_thread()
        for worker in workers:
            worker.stop()
        for worker in workers:
            if worker.is_current_thread(current_thread):
                continue
            worker.join(timeout=1)

    def _run_worker_plan(self, index: int) -> None:
        with self._lock:
            self._running_plans.add(index)
            self._progress.setdefault(index, self._initial_progress(index))
        try:
            self.run_plan_fn(index)
            with self._lock:
                self._last_error = ""
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
            self.log(f"商品兑换计划 {index + 1} 执行失败: {exc}")
        finally:
            with self._lock:
                self._running_plans.discard(index)

    def _next_plan(self) -> tuple[int, int] | None:
        shop = self._shop_config()
        if not shop.get("enable", False):
            return None
        now = int(time.time())
        candidates: list[tuple[int, int]] = []
        for index, plan in enumerate(shop.get("plans") or []):
            if not isinstance(plan, dict):
                continue
            exchange_at = parse_ts(plan.get("exchange_at"))
            if not plan.get("enable", True) or not plan.get("auto", True) or exchange_at <= now:
                continue
            attempt_key = self._attempt_key(plan, exchange_at)
            if str(plan.get("last_attempt_key") or "") == attempt_key:
                continue
            candidates.append((index, exchange_at))
        return min(candidates, key=lambda item: item[1]) if candidates else None

    def _shop_config(self) -> dict[str, Any]:
        shop = self.config.get("shop_exchange", {})
        return shop if isinstance(shop, dict) else {}

    @staticmethod
    def _attempt_key(plan: dict[str, Any], exchange_at: int) -> str:
        """写进计划的 `last_attempt_key`，格式必须保持兼容（已保存的计划里存的就是它）。"""
        return f"{plan.get('goods_id', '')}:{exchange_at}"

    @staticmethod
    def _worker_key(plan: dict[str, Any], exchange_at: int) -> str:
        """计划线程的唯一键：**带上账号**。

        只用「商品:时间」的话，同一商品同一时间的两个账号计划会互相覆盖，
        后一条永远轮不到执行；加上账号后每个账号各有一条线程。
        `last_attempt_key` 仍然用不带账号的 `_attempt_key`，所以不影响已保存的计划。
        """
        account_index = plan.get("account_index", 0)
        return f"{account_index}:{ExchangeScheduler._attempt_key(plan, exchange_at)}"

    @staticmethod
    def duplicate_worker_keys(plans: list[Any]) -> list[str]:
        """找出 worker 键相同的计划（同一账号 + 同一商品 + 同一开抢时间）。"""
        counts: dict[str, int] = {}
        for plan in plans:
            if not isinstance(plan, dict):
                continue
            key = ExchangeScheduler._worker_key(plan, parse_ts(plan.get("exchange_at")))
            counts[key] = counts.get(key, 0) + 1
        return sorted(key for key, count in counts.items() if count > 1)

    def _ip_check(self) -> bool | None:
        """抢购前的出口 IP 判断（非阻塞）：False 就不发这次请求。"""
        return mainland_ok_now(self.config)


class PlanWorker:
    def __init__(
        self,
        index: int,
        plan: dict[str, Any],
        exchange_at: int,
        attempt_key: str,
        run_fn: RunPlanFn,
        log_fn: LogFn,
        ip_check: Callable[[], bool | None] | None = None,
    ) -> None:
        self.index = index
        self.plan = plan
        self.exchange_at = exchange_at
        self.attempt_key = attempt_key
        self.run_fn = run_fn
        self.log = log_fn
        self.ip_check = ip_check
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._loop,
            name=f"miyouqian-exchange-plan-{index + 1}",
            daemon=True,
        )

    def update(self, index: int, plan: dict[str, Any]) -> None:
        """配置重排/编辑后同步计划下标与最新快照（由 reload 调用）。

        线程是长活的（可能要等几小时才到点），所以不能靠「启动时那个下标」办事：
        用户在网页上删掉或调整计划顺序后，旧下标会指向**另一个计划**。
        """
        self.index = index
        self.plan = plan

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def is_current_thread(self, thread: threading.Thread) -> bool:
        return self._thread is thread

    def _loop(self) -> None:
        goods_name = self.plan.get("goods_name") or self.plan.get("goods_id") or f"计划 {self.index + 1}"
        # 出口 IP 守卫：抢购不能从境外 IP 发出去（容易触发异地风控），
        # 但绝不能「等待 IP 恢复」——那会错过开抢。所以只在开抢前 3 分钟检查一次，
        # 境外就直接放弃这一条，不占用任何等待时间。
        if self.ip_check is not None and self.ip_check() is False:
            self.log(f"出口 IP 不在中国大陆，本次跳过兑换计划（避免异地风控）: {goods_name}")
            return
        target_text = format_ts(self.exchange_at)
        time_offset = sync_server_time_offset(self.log)
        now = lambda: time.time() + time_offset
        prewarm_at = max(self.exchange_at - PRECISE_WAIT_SECONDS, 0)
        self.log(f"商品兑换计划线程已创建: {goods_name}，目标时间 {target_text}")
        if not wait_until(prewarm_at, self._stop, now):
            return
        time_offset = sync_server_time_offset(self.log)
        now = lambda: time.time() + time_offset
        self.log(f"商品兑换计划进入精确等待: {goods_name}，校准目标时间 {target_text}")
        while not self._stop.is_set():
            if now() >= self.exchange_at:
                # 用 self.index：它会被 reload() 里的 update() 同步到最新下标，
                # 所以即使计划被重排/删除，这里也不会执行到别的计划。
                self.log(f"商品兑换计划到点触发: {goods_name}")
                self.run_fn(self.index)
                return
            self._stop.wait(CHECK_INTERVAL_SECONDS)


def wait_until(target_ts: int, stop_event: threading.Event, now_fn: Callable[[], float] = time.time) -> bool:
    while not stop_event.is_set():
        delay = target_ts - now_fn()
        if delay <= 0:
            return True
        stop_event.wait(min(delay, 30))
    return False


def sync_server_time_offset(log: LogFn) -> float:
    try:
        before = time.time()
        response = httpx.get(
            c.MALL_GOODS_LIST_URL,
            params={"app_id": 1, "point_sn": "myb", "page_size": 1, "page": 1},
            headers={
                "User-Agent": c.DEFAULT_MOBILE_UA,
                "x-rpc-client_type": "5",
                "Referer": "https://user.mihoyo.com/",
            },
            timeout=10,
        )
        after = time.time()
        response.raise_for_status()
        date_header = response.headers.get("Date")
        if not date_header:
            log("服务器时间校准失败: 响应缺少 Date 头，使用本地时间")
            return 0.0
        # HTTP Date 只有秒级精度（真实服务器时间落在 [t, t+1) 内），取中值 +0.5 秒。
        # 不补这一下就会长期偏慢，把到点触发往后拖最多 1 秒——抢购里这就是决胜的 1 秒。
        server_time = parsedate_to_datetime(date_header).timestamp() + 0.5
        local_midpoint = (before + after) / 2
        offset = server_time - local_midpoint
        log(f"服务器时间校准完成: 偏移 {offset:+.3f}s，RTT {after - before:.3f}s")
        return offset
    except Exception as exc:
        log(f"服务器时间校准失败: {exc}，使用本地时间")
        return 0.0


def parse_ts(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def format_ts(value: int) -> str:
    return datetime.fromtimestamp(value).isoformat(timespec="seconds") if value else ""
