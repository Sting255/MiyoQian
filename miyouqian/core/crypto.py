# -*- coding: utf-8 -*-
"""米游社请求签名工具。"""

from __future__ import annotations

import hashlib
import json
import random
import string
import time
import uuid
from typing import Any

from .. import constants as c


def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def random_text(length: int) -> str:
    return "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(length))


def ds(web: bool = False) -> str:
    salt = c.BBS_WEB_SALT if web else c.BBS_SALT
    t = str(int(time.time()))
    r = random_text(6)
    sign = md5(f"salt={salt}&t={t}&r={r}")
    return f"{t},{r},{sign}"


def ds_x6(query: str = "", body: str = "") -> str:
    t = str(int(time.time()))
    r = str(random.randint(100001, 200000))
    sign = md5(f"salt={c.BBS_X6_SALT}&t={t}&r={r}&b={body}&q={query}")
    return f"{t},{r},{sign}"


def ds_x4(query: str = "", body: str = "") -> str:
    t = str(int(time.time()))
    r = str(random.randint(100000, 200000))
    sign = md5(f"salt={c.PASSPORT_X4_SALT}&t={t}&r={r}&b={body}&q={query}")
    return f"{t},{r},{sign}"


def device_id(seed: str | None = None) -> str:
    if seed:
        return str(uuid.uuid3(uuid.NAMESPACE_URL, seed)).upper()
    return str(uuid.uuid4()).upper()


def device_fp() -> str:
    return "".join(random.choice("0123456789abcdef") for _ in range(13))
