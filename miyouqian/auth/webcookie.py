# -*- coding: utf-8 -*-
"""用 stoken 换网页登录 Cookie（ltoken_v2 / cookie_token_v2 等）。

2026-11-20/21 起 passport 的 getLTokenBySToken 返回 1002、getCookieAccountInfoBySToken 返回 -464，
旧的「stoken 换 ltoken/cookie_token」链路对本程序不再可用（上游 issue #33/#34）。
可用的链路是网页端扫码登录：网页端 createQRLogin 生成二维码，用账号的 stoken 以米游社 App 身份
scanQRLogin + confirmQRLogin，网页端 queryQRLoginStatus 的 Set-Cookie 就是完整的网页登录 Cookie。
整个过程只用账号自己的 stoken，不需要人工扫码。
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .. import constants as c
from ..core import crypto

WEB_PASSPORT = f"{c.PASSPORT_API}/account/ma-cn-passport/web"
APP_PASSPORT = f"{c.PASSPORT_API}/account/ma-cn-passport/app"
WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
# 网页登录下发的 Cookie 名；缺 cookie_token_v2 或 ltoken_v2 视为失败
WEB_COOKIE_NAMES = (
    "account_id", "account_id_v2", "account_mid_v2", "cookie_token_v2",
    "ltmid_v2", "ltoken_v2", "ltuid", "ltuid_v2",
)
REQUIRED_COOKIES = ("cookie_token_v2", "ltoken_v2")


class WebCookieError(RuntimeError):
    pass


def _check(data: dict[str, Any], step: str) -> dict[str, Any]:
    if data.get("retcode") != 0:
        raise WebCookieError(f"{step}失败: retcode={data.get('retcode')} message={data.get('message')}")
    payload = data.get("data")
    return payload if isinstance(payload, dict) else {}


def _app_headers(device: dict[str, Any], stuid: str, stoken: str, mid: str, body: str) -> dict[str, str]:
    headers = {
        "x-rpc-app_id": c.PASSPORT_APP_ID,
        "x-rpc-client_type": "2",
        "x-rpc-app_version": c.BBS_VERSION,
        "x-rpc-device_id": str(device.get("id") or ""),
        "x-rpc-device_model": str(device.get("model") or ""),
        "x-rpc-device_name": str(device.get("name") or ""),
        "user-agent": f"Mozilla/5.0 (Linux; Android 14) Mobile miHoYoBBS/{c.BBS_VERSION}",
        "content-type": "application/json",
        "cookie": f"stuid={stuid};stoken={stoken};mid={mid}",
        "ds": crypto.ds_x4(body=body),
    }
    if device.get("fp"):
        headers["x-rpc-device_fp"] = str(device["fp"])
    return headers


def _web_headers(device: dict[str, Any]) -> dict[str, str]:
    return {
        "x-rpc-app_id": c.PASSPORT_APP_ID,
        "x-rpc-client_type": "4",
        "x-rpc-device_id": str(device.get("id") or "").lower(),
        "user-agent": WEB_UA,
        "origin": "https://user.mihoyo.com",
        "referer": "https://user.mihoyo.com/",
        "content-type": "application/json",
    }


def fetch_web_cookies(
    stuid: str,
    stoken: str,
    mid: str,
    device: dict[str, Any] | None = None,
    *,
    timeout: float = 15.0,
    poll_seconds: float = 20.0,
) -> dict[str, str]:
    """返回网页登录 Cookie 字典。失败抛 WebCookieError（不含任何凭证内容）。"""
    if not (stuid and stoken and mid):
        raise WebCookieError("缺少 stuid/stoken/mid，无法换取网页凭证")
    device = device or {}
    try:
        with httpx.Client(timeout=timeout) as web, httpx.Client(timeout=timeout) as app:
            payload = _check(web.post(f"{WEB_PASSPORT}/createQRLogin", headers=_web_headers(device), json={}).json(),
                             "生成网页登录二维码")
            ticket = str(payload.get("ticket") or "")
            query = parse_qs(urlparse(str(payload.get("url") or "")).query)
            token_types = [int(x) for x in (query.get("token_types") or ["4"])[0].split(",") if x.isdigit()]
            if not ticket:
                raise WebCookieError("生成网页登录二维码失败: 接口未返回 ticket")
            for step, name in (("scanQRLogin", "确认扫码"), ("confirmQRLogin", "确认登录")):
                body = json.dumps({"ticket": ticket, "token_types": token_types}, separators=(",", ":"))
                resp = app.post(f"{APP_PASSPORT}/{step}", headers=_app_headers(device, stuid, stoken, mid, body),
                                content=body)
                _check(resp.json(), name)
            deadline = time.monotonic() + poll_seconds
            while True:
                status = _check(web.post(f"{WEB_PASSPORT}/queryQRLoginStatus", headers=_web_headers(device),
                                         json={"ticket": ticket}).json(), "查询网页登录状态")
                if status.get("status") == "Confirmed":
                    break
                if time.monotonic() > deadline:
                    raise WebCookieError(f"网页登录未确认（状态 {status.get('status')}）")
                time.sleep(1.5)
            jar = {name: web.cookies.get(name) for name in WEB_COOKIE_NAMES}
    except WebCookieError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise WebCookieError(f"网络或接口请求异常（{type(exc).__name__}）") from None
    cookies = {k: str(v) for k, v in jar.items() if v}
    missing = [name for name in REQUIRED_COOKIES if not cookies.get(name)]
    if missing:
        raise WebCookieError(f"网页登录未下发 {', '.join(missing)}")
    return cookies


def build_cookie_header(web_cookies: dict[str, str], stuid: str, mid: str) -> str:
    """网页 Cookie + 旧版字段别名（老接口仍读 cookie_token / ltoken / account_id）。"""
    merged = dict(web_cookies)
    merged.setdefault("account_id", stuid)
    merged.setdefault("account_id_v2", stuid)
    merged.setdefault("ltuid", stuid)
    merged.setdefault("ltuid_v2", stuid)
    merged.setdefault("account_mid_v2", mid)
    merged.setdefault("ltmid_v2", mid)
    merged["cookie_token"] = merged["cookie_token_v2"]
    merged["ltoken"] = merged["ltoken_v2"]
    merged["login_uid"] = stuid
    return "; ".join(f"{k}={v}" for k, v in merged.items())
