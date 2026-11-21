# -*- coding: utf-8 -*-
"""向官方 getFp 申请服务器签发的 device_fp。

本地随机生成的 13 位十六进制 fp 没在服务器登记过：登录本身能过，
但用它拿到的 stoken 去 passport 换 ltoken / cookie_token 会被拒（retcode=1002），
已有会话也可能被风控整批踢下线（-100）。所以登录前必须先拿到服务器签发的 fp。
请求格式与上游 Marchen-orz/MiyoQian 的 core/device.py 一致（platform=2 / app_name=bbs_cn，
ext_fields 里 oaid 与 board 必须非空，否则服务器回 code=500 并原样回显种子）。
"""

from __future__ import annotations

import json
import random
import re
import time
import uuid
from typing import Any

from .. import constants as c
from .http import ApiClient

FP_SOURCE_SERVER = "server"
_FP_RE = re.compile(r"^[0-9a-fA-F]{13}$")

# 型号 -> SoC 平台代号（Build.BOARD）。服务器只要求非空，不在表里的机型填 unknown。
_BOARDS = {
    "23127PN0CC": "pineapple", "2211133C": "kalama", "2311DRK48C": "kalama", "23013RK75C": "cape",
    "PJD110": "pineapple", "PHZ110": "mt6989", "V2309A": "mt6989", "SM-S9110": "kalama",
    "2312DRA50C": "parrot",
}
_MANUFACTURERS = {"Redmi": "Xiaomi", "POCO": "Xiaomi"}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def has_server_fp(device: dict[str, Any]) -> bool:
    return device.get("fp_source") == FP_SOURCE_SERVER and bool(_FP_RE.match(_text(device.get("fp"))))


def _os_version(device: dict[str, Any]) -> str:
    return _text(device.get("os_version")) or "14"


def build_fp_request(device: dict[str, Any]) -> dict[str, str]:
    bbs_device_id = _text(device.get("id"))
    model = _text(device.get("model")) or "23127PN0CC"
    brand = _text(device.get("brand")) or (_text(device.get("name")).split() or ["Xiaomi"])[0]
    os_version = _os_version(device)
    major = int(re.match(r"\d+", os_version).group()) if re.match(r"\d+", os_version) else 14
    ext = {
        "oaid": str(uuid.uuid5(uuid.NAMESPACE_URL, f"oaid:{bbs_device_id}")),
        "board": _BOARDS.get(model, "unknown"),
        "brand": brand,
        "manufacturer": _MANUFACTURERS.get(brand, brand),
        "model": model,
        "osVersion": os_version,
        "sdkVersion": str(major + 19 if major <= 12 else major + 20),
        "packageName": "com.mihoyo.hyperion",
        "packageVersion": c.BBS_VERSION,
    }
    return {
        # getFp 的 device_id 是 Android ID 形态（16 位十六进制），由 bbs device_id 稳定推出
        "device_id": uuid.uuid5(uuid.NAMESPACE_URL, f"android_id:{bbs_device_id}").hex[:16],
        "seed_id": str(uuid.uuid4()),
        "seed_time": str(int(time.time() * 1000)),
        "platform": "2",
        # 这里的 device_fp 只是种子，服务器会另发一个
        "device_fp": "".join(random.choice("0123456789abcdef") for _ in range(13)),
        "app_name": "bbs_cn",
        "ext_fields": json.dumps(ext, ensure_ascii=False, separators=(",", ":")),
        "bbs_device_id": bbs_device_id,
    }


def request_server_fp(client: ApiClient, device: dict[str, Any]) -> str:
    """申请一次；code 不是 200 时 device_fp 只是回显的种子，不能当成功。"""
    body = build_fp_request(device)
    data = client.post_json(c.DEVICE_FP_URL, json=body)
    raw = data.get("data") if isinstance(data.get("data"), dict) else {}
    fp = _text(raw.get("device_fp"))
    if data.get("retcode") != 0 or str(raw.get("code")) != "200" or not _FP_RE.match(fp) or fp == body["device_fp"]:
        raise ValueError(str(raw.get("msg") or data.get("message") or "接口没有返回有效的 device_fp"))
    return fp


def ensure_server_fp(client: ApiClient, device: dict[str, Any], *, force: bool = False,
                     attempts: int = 3) -> tuple[bool, str]:
    """没有服务器签发的 fp 就申请一枚并写回 device（调用方负责保存配置）。返回 (是否已有, 失败原因)。"""
    if not force and has_server_fp(device):
        return True, ""
    reason = ""
    for attempt in range(max(attempts, 1)):
        try:
            fp = request_server_fp(client, device)
        except Exception as exc:  # noqa: BLE001 - 失败原因只用于展示
            reason = str(exc) or type(exc).__name__
            if attempt + 1 < attempts:
                time.sleep(1.0)
            continue
        device.update({"fp": fp, "fp_source": FP_SOURCE_SERVER, "fp_time": int(time.time())})
        return True, ""
    return False, reason
