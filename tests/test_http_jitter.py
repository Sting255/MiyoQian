# -*- coding: utf-8 -*-
"""P1：抢购/交互路径不能被「模拟真人」的全局抖动拖死。

背景：`ApiClient` 默认在每个请求前随机 sleep 3–7 秒（防风控用，签到路径必须保留）。
但抢购链路（ExchangeScheduler 把时间校准到亚秒级后精确触发）和 Web 控制台的
即时操作都复用同一个默认值，导致：
  - 到点后的第一个兑换请求还要先睡 3–7 秒；
  - shop_exchange.retry_interval(0.4s) 形同虚设，20 秒窗口只够发 3~4 次。
"""

from __future__ import annotations

import threading
import unittest
from unittest import mock

from miyouqian.core.http import ApiClient
from miyouqian.tasks.shop_exchange import ShopExchange
from tests.support import base_config


class FakeClock:
    """把 sleep 变成推进虚拟时间，测试不真等。"""

    def __init__(self) -> None:
        self.now = 1_700_000_000.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        seconds = max(float(seconds), 0.0)
        self.sleeps.append(seconds)
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


class StubResponse:
    headers: dict = {}

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return {"retcode": 1001, "message": "库存不足"}


class StubHttpClient:
    """替掉 httpx.Client：不联网，只统计请求次数。"""

    def __init__(self) -> None:
        self.calls = 0

    def request(self, method: str, url: str, **kwargs: object) -> StubResponse:
        self.calls += 1
        return StubResponse()

    def close(self) -> None:
        pass


class HttpJitterTest(unittest.TestCase):
    def test_checkin_client_keeps_jitter(self) -> None:
        """签到路径的防风控抖动必须保留，不能被顺手关掉。"""
        self.assertEqual(ApiClient().jitter_range, (3.0, 7.0))

    def test_shop_client_has_no_jitter(self) -> None:
        """抢购/即时操作用的客户端必须不带抖动。"""
        from miyouqian.core.http import shop_client

        self.assertFalse(shop_client().jitter_range, "抢购客户端不应有请求前抖动")

    def test_web_shop_exchange_uses_jitterless_client(self) -> None:
        """Web 层发起兑换时实际传下去的客户端必须是无抖动的那个。"""
        from miyouqian.service import web as web_mod

        seen: dict = {}

        class CaptureShop:
            def __init__(self, client, config, account=None, emit=None):
                seen["jitter"] = client.jitter_range

            def exchange_with_retry(self, plan, on_progress=None):
                return {"ok": True, "retcode": 0, "message": "兑换成功", "attempt": 1}

        app = web_mod.WebApp.__new__(web_mod.WebApp)  # 只测调用点，不启服务
        app.config = base_config()
        app.lock = threading.RLock()
        app.log = lambda *a, **k: None
        app._account_by_index = lambda index: {"name": "A", "cookie": "c", "stuid": "1"}
        app._send_exchange_push = lambda *a, **k: None

        plan = {"goods_id": "100", "device_fp": "fp", "account_index": 0}
        with mock.patch.object(web_mod, "ShopExchange", CaptureShop):
            web_mod.WebApp.shop_exchange_once(app, plan)

        self.assertIn("jitter", seen, "shop_exchange_once 没有把客户端传给 ShopExchange")
        self.assertFalse(seen["jitter"], "Web 兑换路径仍在用带 3–7 秒抖动的客户端")

    def test_retry_cadence_is_governed_by_retry_interval(self) -> None:
        """重试窗口内的请求次数应由 retry_interval 决定，而不是被抖动吃掉。"""
        from miyouqian.core.http import shop_client

        clock = FakeClock()
        config = base_config()
        config["shop_exchange"].update({"retry_seconds": 2.0, "retry_interval": 0.2})

        client = shop_client(timeout=5.0)
        http = StubHttpClient()
        client._client = http  # type: ignore[assignment]

        exchange = ShopExchange(client, config, {}, emit=None)
        try:
            with (
                mock.patch("miyouqian.tasks.shop_exchange.time", clock),
                mock.patch("miyouqian.core.http.time.sleep", side_effect=clock.sleep),
            ):
                result = exchange.exchange_with_retry({"goods_id": "100", "device_fp": "fp"})
        finally:
            client.close()

        self.assertFalse(result["ok"])
        self.assertGreaterEqual(http.calls, 5, "重试窗口内请求次数过少（被抖动吃掉了？）")
        self.assertLess(
            max(clock.sleeps),
            1.0,
            f"单次请求前仍在长时间 sleep：{max(clock.sleeps):.2f}s",
        )

    def test_retry_interval_is_the_upper_bound_of_the_random_gap(self) -> None:
        """填的数就是「最多隔多久」：实际等待落在 0 ~ 该值 之间，且能接近两头。"""
        from miyouqian.core.http import shop_client
        from miyouqian.tasks.shop_exchange import shop_retry_interval

        self.assertEqual(shop_retry_interval({"retry_interval": 0.3}), 0.3)
        self.assertEqual(shop_retry_interval({}), 0.4, "缺失时用 0.4 兜底")
        self.assertEqual(shop_retry_interval({"retry_interval": 0}), 0.0, "0 表示完全不等待")
        self.assertEqual(shop_retry_interval({"retry_interval": -2}), 0.0, "负数夹到 0")
        self.assertEqual(shop_retry_interval({"retry_interval": "abc"}), 0.4, "垃圾值用兜底")
        self.assertEqual(shop_retry_interval({"retry_interval": None}), 0.4)

        clock = FakeClock()
        config = base_config()
        config["shop_exchange"].update({"retry_seconds": 200.0, "retry_interval": 0.5})

        client = shop_client(timeout=5.0)
        client._client = StubHttpClient()  # type: ignore[assignment]
        exchange = ShopExchange(client, config, {}, emit=None)
        try:
            with (
                mock.patch("miyouqian.tasks.shop_exchange.time", clock),
                mock.patch("miyouqian.core.http.time.sleep", side_effect=clock.sleep),
                mock.patch.object(
                    exchange, "exchange", lambda plan: {"ok": False, "retcode": 1, "message": "售罄"}
                ),
            ):
                exchange.exchange_with_retry({"goods_id": "100", "device_fp": "fp"})
        finally:
            client.close()

        gaps = [value for value in clock.sleeps if value > 0]
        self.assertGreater(len(gaps), 50, "样本太少，说明循环没跑起来")
        self.assertLessEqual(max(gaps), 0.5, f"等待超过了填写的上限：{max(gaps):.3f}")
        self.assertLess(min(gaps), 0.05, f"最小值没有接近 0（分布不对）：{min(gaps):.3f}")
        self.assertGreater(max(gaps), 0.4, f"最大值没有接近上限：{max(gaps):.3f}")
        mean = sum(gaps) / len(gaps)
        self.assertTrue(0.2 < mean < 0.32, f"均值应接近上限的一半（0.25）：{mean:.3f}")


if __name__ == "__main__":
    unittest.main()
