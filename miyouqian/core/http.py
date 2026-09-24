# -*- coding: utf-8 -*-
"""HTTP 客户端封装。"""

from __future__ import annotations

import random
import time
from typing import Any

import httpx


class ApiError(RuntimeError):
    """接口或网络错误。"""


# 签到路径专用的「模拟真人」抖动范围（秒）：每次请求前随机等待，
# 让多账号签到看起来更像人在手动操作。
DEFAULT_JITTER_RANGE: tuple[float, float] = (3.0, 7.0)


def shop_client(timeout: float = 15.0) -> "ApiClient":
    """抢购 / 立即执行这类路径用的客户端：**不做请求前抖动**。

    ExchangeScheduler 已经把时间校准到亚秒级再精确触发，
    再随机睡 3–7 秒会直接错过抢购窗口；
    而且 shop_exchange.retry_interval 也会被这个等待彻底盖掉
    （20 秒窗口只够发 3~4 次请求）。
    """
    return ApiClient(timeout=timeout, jitter_range=None)


class ApiClient:
    def __init__(
        self,
        timeout: float = 30.0,
        jitter_range: tuple[float, float] | None = DEFAULT_JITTER_RANGE,
    ) -> None:
        # 每次 HTTP 请求前的随机抖动（秒），模拟真人操作节奏
        # 传 None 或 (0, 0) 可禁用
        # 默认 (3, 7)：平均 5 秒，让签到操作看起来更像人在手动操作
        self.jitter_range = jitter_range
        transport = httpx.HTTPTransport(retries=3)
        self._client = httpx.Client(
            timeout=timeout,
            transport=transport,
            follow_redirects=True,
        )

    def get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("GET", url, **kwargs)

    def post_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        return self._request_json("POST", url, **kwargs)

    def get_json_with_headers(self, url: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Headers]:
        return self._request_json_with_headers("GET", url, **kwargs)

    def post_json_with_headers(self, url: str, **kwargs: Any) -> tuple[dict[str, Any], httpx.Headers]:
        return self._request_json_with_headers("POST", url, **kwargs)

    def _request_json(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        data, _headers = self._request_json_with_headers(method, url, **kwargs)
        return data

    def _request_json_with_headers(
        self, method: str, url: str, **kwargs: Any
    ) -> tuple[dict[str, Any], httpx.Headers]:
        # 模拟真人操作的请求间随机抖动
        if self.jitter_range:
            low, high = self.jitter_range
            if high > low >= 0:
                time.sleep(random.uniform(low, high))
        try:
            response = self._client.request(method, url, **kwargs)
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            raise ApiError(f"网络请求失败: {url} ({exc})") from exc
        except ValueError as exc:
            raise ApiError(f"接口返回不是 JSON: {url}") from exc
        if not isinstance(data, dict):
            raise ApiError(f"接口返回格式异常: {url}")
        return data, response.headers

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ApiClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
