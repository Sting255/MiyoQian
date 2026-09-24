# -*- coding: utf-8 -*-
"""配置加载与保存。"""

from __future__ import annotations

import copy
import pathlib
import random
from typing import Any

import yaml

from ..constants import BBS_FORUMS, GAMES
from . import crypto

SENSITIVE_ACCOUNT_FIELDS = ("cookie", "stuid", "stoken", "mid")
SUPPORTED_CLOUD_GAME_KEYS = ("genshin", "zzz")
SUPPORTED_GAME_KEYS = tuple(GAMES)
# 账号间等待的上限：24 小时。再长就会把后面的账号推到第二天的定时任务上。
MAX_ACCOUNT_GAP_MINUTES = 24 * 60
SUPPORTED_FORUM_KEYS = tuple(str(key) for key in BBS_FORUMS)
ACCOUNT_TASK_SECTIONS = ("features", "games", "cloud_games", "bbs")
FEATURE_KEYS = ("game_checkin", "cloud_game_checkin", "bbs_tasks")

DEVICE_PRESET_KEYS = ("name", "model")

# 设备指纹预设。这些机型名与型号只作为请求头里的设备标识使用，
# 更换后可以让同一个 IP 下的多账号看起来来自不同设备，降低被风控的概率。
# 列表里都是市面上常见的安卓机型，可以在配置文件中自行增删。
DEFAULT_DEVICE_PRESETS: list[dict[str, str]] = [
    # 小米 / Redmi
    {"name": "Xiaomi 14", "model": "23127PN0CC"},
    {"name": "Xiaomi 14 Pro", "model": "23116PN5BC"},
    {"name": "Xiaomi 13", "model": "2211133C"},
    {"name": "Xiaomi 13 Pro", "model": "2210132C"},
    {"name": "Xiaomi 12", "model": "2201123C"},
    {"name": "Redmi K70", "model": "2311DRK48C"},
    {"name": "Redmi K70 Pro", "model": "23117RK66C"},
    {"name": "Redmi K60", "model": "23013RK75C"},
    {"name": "Redmi Note 13 Pro", "model": "2312DRA50C"},
    {"name": "Redmi Note 12 Turbo", "model": "23049RAD8C"},
    # OPPO / OnePlus / realme
    {"name": "OPPO Find X7", "model": "PHZ110"},
    {"name": "OPPO Find X6", "model": "PGFM10"},
    {"name": "OPPO Reno11", "model": "PJH110"},
    {"name": "OnePlus 12", "model": "PJD110"},
    {"name": "OnePlus 11", "model": "PHB110"},
    {"name": "OnePlus Ace 2", "model": "PHK110"},
    {"name": "realme GT5", "model": "RMX3820"},
    {"name": "realme GT Neo5", "model": "RMX3706"},
    # vivo / iQOO
    {"name": "vivo X100", "model": "V2309A"},
    {"name": "vivo X100 Pro", "model": "V2324A"},
    {"name": "vivo X90", "model": "V2218A"},
    {"name": "vivo S17", "model": "V2283A"},
    {"name": "iQOO 12", "model": "V2307A"},
    {"name": "iQOO Neo8", "model": "V2301A"},
    # 荣耀 / 华为
    {"name": "HONOR 100", "model": "MAA-AN00"},
    {"name": "HONOR Magic5", "model": "PGT-AN00"},
    {"name": "HONOR 90", "model": "REA-AN00"},
    {"name": "HUAWEI Mate 60", "model": "BRA-AL00"},
    {"name": "HUAWEI P60", "model": "LNA-AL00"},
    {"name": "HUAWEI nova 11", "model": "GOA-AL80"},
    # 三星 / 其他
    {"name": "Samsung Galaxy S23", "model": "SM-S9110"},
    {"name": "Samsung Galaxy S23 Ultra", "model": "SM-S9180"},
    {"name": "Samsung Galaxy S24", "model": "SM-S9210"},
    {"name": "Samsung Galaxy A54", "model": "SM-A5460"},
    {"name": "Meizu 20", "model": "M381Q"},
    {"name": "nubia Z50", "model": "NX711J"},
    {"name": "Google Pixel 8", "model": "GKWS6"},
    {"name": "Google Pixel 7", "model": "GVU6C"},
    {"name": "Nothing Phone (2)", "model": "A065"},
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enable": True,
    "accounts": [],
    "storage": {
        "data_dir": "data",
        "credentials_file": "credentials.yaml",
        "log_dir": "logs",
        "log_file": "miyouqian.log",
    },
    "device": {"id": "", "fp": "", "name": "", "model": "", "presets": DEFAULT_DEVICE_PRESETS},
    "features": {"game_checkin": True, "cloud_game_checkin": False, "bbs_tasks": False},
    "captcha": {
        "enable": False,
        "max_retries": 3,
        "channels": [{"provider": "damagou", "enable": False, "userkey": "", "type": "", "timeout": 60}],
    },
    "ip_guard": {
        "enable": True,
        "check_interval": 300,
        "max_wait": 7200,
        "notify": True,
        "on_error": "allow",
        "endpoints": [],
    },
    "schedule": {"enable": False, "time": "09:00", "jitter_minutes": 45, "run_on_start": False},
    # 账号之间的防风控随机等待（分钟）。默认 60~120 分钟，与旧版本硬编码的
    # runner.ACCOUNT_GAP_RANGE = (3600, 7200) 一致。enable=false 或上下限都为 0
    # 就表示所有账号连着跑、不等待。
    "account_gap": {"enable": True, "min_minutes": 60, "max_minutes": 120},
    "games": {
        "enabled": ["genshin", "starrail", "zzz"],
        "black_list": {"genshin": [], "starrail": [], "zzz": []},
    },
    "cloud_games": {
        "enabled": ["genshin", "zzz"],
    },
    "bbs": {
        "forums": [5, 2],
        "checkin": True,
        "read": False,
        "like": False,
        "share": False,
        "cancel_like": True,
        "post_limit": 5,
        "delay_seconds": [1, 3],
    },
    "push": {
        "enable": False,
        "error_only": False,
        "per_account": False,
        "channels": [],
    },
    "shop_exchange": {
        "enable": True,
        "retry_seconds": 20,
        "retry_interval": 0.4,
        "plans": [],
    },
    "web": {
        "host": "127.0.0.1",
        "port": 5890,
        "password": "",
    },
}


def load_config(path: str | pathlib.Path) -> dict[str, Any]:
    config_path = pathlib.Path(path)
    if not config_path.exists():
        config = copy.deepcopy(DEFAULT_CONFIG)
        normalize_config(config)
        return config
    with config_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"配置文件格式错误: {config_path}")
    config = merge_dict(copy.deepcopy(DEFAULT_CONFIG), loaded)
    normalize_config(config)
    merge_credentials(config, load_credentials(config_path, config))
    normalize_config(config)
    return config


def save_config(path: str | pathlib.Path, config: dict[str, Any]) -> None:
    normalize_config(config)
    config_path = pathlib.Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_credentials(credentials_path(config_path, config), config)
    public_config = strip_credentials(config)
    with config_path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(public_config, file, allow_unicode=True, sort_keys=False)


def create_config(path: str | pathlib.Path, force: bool = False) -> pathlib.Path:
    config_path = pathlib.Path(path)
    if config_path.exists() and not force:
        raise FileExistsError(f"配置已存在: {config_path}")
    config = copy.deepcopy(DEFAULT_CONFIG)
    save_config(config_path, config)
    return config_path


def merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge_dict(base[key], value)
        else:
            base[key] = value
    return base


def merge_device_presets(value: Any) -> list[dict[str, str]]:
    """把内置机型预设合并进现有列表，保留用户自己添加的机型。"""
    presets: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in list(value) if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        model = str(item.get("model") or "").strip()
        if not name or not model or name in seen:
            continue
        seen.add(name)
        presets.append({"name": name, "model": model})
    for item in DEFAULT_DEVICE_PRESETS:
        if item["name"] in seen:
            continue
        seen.add(item["name"])
        presets.append(dict(item))
    return presets or copy.deepcopy(DEFAULT_DEVICE_PRESETS)


# 顶层段落 → 内置默认值。用于把「留空」和「写错类型」的段落拉回可用状态。
SECTION_DEFAULTS: dict[str, Any] = {
    key: value for key, value in DEFAULT_CONFIG.items() if isinstance(value, dict)
}


def normalize_config(config: dict[str, Any]) -> None:
    # ---- 顶层段落兜底 ----------------------------------------------------
    # YAML 里把键留空（`accounts:` / `device:`）读出来是 None，
    # 而 setdefault 在「键已存在」时不会替换它，于是后面
    # enumerate(None) / None.get(...) 直接崩；把段落写成字符串、列表也一样。
    # 这里统一退回内置默认值，保证 normalize 之后每个段落都是期望的类型。
    for key, default in SECTION_DEFAULTS.items():
        if not isinstance(config.get(key), dict):
            config[key] = copy.deepcopy(default)
    accounts = config.get("accounts")
    if isinstance(accounts, dict):
        config["accounts"] = [accounts]
    elif not isinstance(accounts, list):
        config["accounts"] = []
    # enable 留空时按默认「开启」处理，不能因为 None 是假值就整天不跑任务
    raw_enable = config.get("enable")
    config["enable"] = True if raw_enable is None else parse_bool(raw_enable)

    storage = config.setdefault("storage", {})
    storage.setdefault("data_dir", "data")
    storage.setdefault("credentials_file", "credentials.yaml")
    storage.setdefault("log_dir", "logs")
    storage.setdefault("log_file", "miyouqian.log")
    cloud_games = config.setdefault("cloud_games", {})
    cloud_games["enabled"] = normalize_cloud_game_enabled(cloud_games.get("enabled", SUPPORTED_CLOUD_GAME_KEYS))
    for index, account in enumerate(config["accounts"], start=1):
        account["name"] = str(account.get("name") or "")[:10]
        for field in SENSITIVE_ACCOUNT_FIELDS:
            account.setdefault(field, "")
        normalize_account_tasks(account)
    device = config.setdefault("device", {})
    first_cookie = str(config["accounts"][0].get("cookie", "")) if config["accounts"] else ""
    device["presets"] = merge_device_presets(device.get("presets"))
    presets = device["presets"]
    if not device.get("name") or not device.get("model"):
        preset = random.choice([item for item in presets if isinstance(item, dict)] or DEFAULT_DEVICE_PRESETS)
        device["name"] = str(preset.get("name") or DEFAULT_DEVICE_PRESETS[0]["name"])
        device["model"] = str(preset.get("model") or DEFAULT_DEVICE_PRESETS[0]["model"])
    if not device.get("id"):
        device["id"] = crypto.device_id(first_cookie or None)
    if not device.get("fp"):
        device["fp"] = crypto.device_fp()
    captcha = config.setdefault("captcha", {})
    captcha["channels"] = normalize_captcha_channels(captcha)
    captcha["enable"] = any(channel.get("enable") for channel in captcha["channels"])
    try:
        captcha["max_retries"] = max(int(captcha.get("max_retries") or 3), 1)
    except (TypeError, ValueError):
        captcha["max_retries"] = 3
    normalize_ip_guard(config)
    normalize_account_gap(config)
    normalize_schedule(config)
    push = config.setdefault("push", {})
    push["channels"] = normalize_push_channels(push)
    push["enable"] = any(channel.get("enable") for channel in push["channels"])
    push["error_only"] = bool(push.get("error_only", False))
    push["per_account"] = parse_bool(push.get("per_account", False))
    web = config.setdefault("web", {})
    web.setdefault("host", "127.0.0.1")
    web.setdefault("port", 5890)
    web.setdefault("password", "")
    features = config.setdefault("features", {})
    features.setdefault("game_checkin", True)
    features.setdefault("cloud_game_checkin", False)
    features.setdefault("bbs_tasks", False)
    bbs = config.setdefault("bbs", {})
    if not isinstance(bbs, dict):
        bbs = {}
        config["bbs"] = bbs
    bbs["checkin"] = parse_bool(bbs.get("checkin", True))
    bbs["read"] = False
    bbs["like"] = False
    bbs["share"] = False
    normalize_shop_exchange(config)


def normalize_shop_exchange(config: dict[str, Any]) -> None:
    shop = config.setdefault("shop_exchange", {})
    if not isinstance(shop, dict):
        shop = {}
        config["shop_exchange"] = shop
    shop["enable"] = parse_bool(shop.get("enable", True))
    try:
        shop["retry_seconds"] = max(float(shop.get("retry_seconds", 20)), 0)
    except (TypeError, ValueError):
        shop["retry_seconds"] = 20
    try:
        shop["retry_interval"] = max(float(shop.get("retry_interval", 0.4)), 0.05)
    except (TypeError, ValueError):
        shop["retry_interval"] = 0.4
    raw_plans = shop.get("plans")
    if not isinstance(raw_plans, list):
        raw_plans = []
    plans: list[dict[str, Any]] = []
    for raw in raw_plans:
        if not isinstance(raw, dict):
            continue
        try:
            account_index = max(int(raw.get("account_index", 0)), 0)
        except (TypeError, ValueError):
            account_index = 0
        try:
            exchange_at = int(float(raw.get("exchange_at") or 0))
        except (TypeError, ValueError):
            exchange_at = 0
        plan = {
            "enable": parse_bool(raw.get("enable", True)),
            "auto": parse_bool(raw.get("auto", True)),
            "account_index": account_index,
            "goods_id": str(raw.get("goods_id") or "").strip(),
            "goods_name": str(raw.get("goods_name") or raw.get("name") or "").strip(),
            "icon": str(raw.get("icon") or ""),
            "price": parse_int(raw.get("price"), 0),
            "stock": str(raw.get("stock") or ""),
            "type": parse_int(raw.get("type"), 0),
            "exchange_at": exchange_at,
            "game": str(raw.get("game") or ""),
            "game_biz": str(raw.get("game_biz") or ""),
            "device_fp": str(raw.get("device_fp") or ""),
            "uid": str(raw.get("uid") or "").strip(),
            "region": str(raw.get("region") or "").strip(),
            "role_name": str(raw.get("role_name") or "").strip(),
            "region_name": str(raw.get("region_name") or "").strip(),
            "address_id": str(raw.get("address_id") or "").strip(),
            "last_result": str(raw.get("last_result") or ""),
            "last_attempt_key": str(raw.get("last_attempt_key") or ""),
            "last_run": str(raw.get("last_run") or ""),
        }
        if plan["goods_id"]:
            plans.append(plan)
    shop["plans"] = plans


def normalize_cloud_game_tokens(value: Any) -> dict[str, str]:
    tokens = value if isinstance(value, dict) else {}
    return {key: str(tokens.get(key) or "") for key in SUPPORTED_CLOUD_GAME_KEYS}


def normalize_cloud_game_enabled(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = list(SUPPORTED_CLOUD_GAME_KEYS)
    enabled: list[str] = []
    for item in raw_items:
        key = str(item or "").strip()
        if key in SUPPORTED_CLOUD_GAME_KEYS and key not in enabled:
            enabled.append(key)
    return enabled


def normalize_game_enabled(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    enabled: list[str] = []
    for item in raw_items:
        key = str(item or "").strip()
        if key in SUPPORTED_GAME_KEYS and key not in enabled:
            enabled.append(key)
    return enabled


def normalize_game_black_list(value: Any) -> dict[str, list[str]]:
    black_list: dict[str, list[str]] = {}
    for key, items in (value or {}).items():
        game = str(key or "").strip()
        if game not in SUPPORTED_GAME_KEYS:
            continue
        uids: list[str] = []
        for item in items if isinstance(items, list) else []:
            uid = str(item or "").strip()
            if uid and uid not in uids:
                uids.append(uid)
        black_list[game] = uids
    return black_list


def normalize_forum_list(value: Any) -> list[int] | None:
    if value is None:
        return None
    if isinstance(value, (str, int)):
        raw_items: list[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        return []
    forums: list[int] = []
    for item in raw_items:
        key = str(item or "").strip()
        if key in SUPPORTED_FORUM_KEYS:
            number = int(key)
            if number not in forums:
                forums.append(number)
    return forums


def normalize_account_tasks(account: dict[str, Any]) -> None:
    """归一化账号级任务配置。只保留允许的字段，空配置会被移除（表示跟随全局）。"""
    raw = account.get("tasks")
    if not isinstance(raw, dict):
        account.pop("tasks", None)
        return
    tasks: dict[str, Any] = {}

    features = raw.get("features")
    if isinstance(features, dict):
        tasks["features"] = {
            "game_checkin": parse_bool(features.get("game_checkin", True)),
            "cloud_game_checkin": parse_bool(features.get("cloud_game_checkin", False)),
            "bbs_tasks": parse_bool(features.get("bbs_tasks", False)),
        }

    games = raw.get("games")
    if isinstance(games, dict):
        section: dict[str, Any] = {}
        if "enabled" in games:
            section["enabled"] = normalize_game_enabled(games.get("enabled"))
        black_list = games.get("black_list")
        if isinstance(black_list, dict):
            section["black_list"] = normalize_game_black_list(black_list)
        if section:
            tasks["games"] = section

    cloud_games = raw.get("cloud_games")
    if isinstance(cloud_games, dict) and "enabled" in cloud_games:
        tasks["cloud_games"] = {"enabled": normalize_cloud_game_enabled(cloud_games.get("enabled"))}

    bbs = raw.get("bbs")
    if isinstance(bbs, dict):
        section = {}
        if "checkin" in bbs:
            section["checkin"] = parse_bool(bbs.get("checkin", True))
        forums = normalize_forum_list(bbs.get("forums"))
        if forums is not None:
            section["forums"] = forums
        if section:
            tasks["bbs"] = section

    if any(tasks.get(section) for section in ACCOUNT_TASK_SECTIONS):
        account["tasks"] = tasks
    else:
        account.pop("tasks", None)


def account_has_tasks(account: dict[str, Any]) -> bool:
    if not isinstance(account, dict):
        return False
    tasks = account.get("tasks")
    return isinstance(tasks, dict) and any(tasks.get(section) for section in ACCOUNT_TASK_SECTIONS)


def account_config(config: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    """把账号级任务配置覆盖到全局配置上，返回该账号实际执行时使用的配置。"""
    effective = copy.deepcopy(config)
    if not account_has_tasks(account):
        return effective
    tasks = account["tasks"]
    for section in ACCOUNT_TASK_SECTIONS:
        value = tasks.get(section)
        if not isinstance(value, dict):
            continue
        target = effective.get(section)
        if not isinstance(target, dict):
            target = {}
            effective[section] = target
        merge_dict(target, copy.deepcopy(value))
    normalize_config(effective)
    return effective


PUSH_CHANNEL_FIELDS: dict[str, tuple[str, ...]] = {
    "pushplus": ("token", "topic"),
    "qq": ("push_url", "access_token", "send_id", "msg_type"),
    "telegram": ("token", "chat_id"),
    "dingrobot": ("webhook", "secret"),
    "feishubot": ("webhook",),
    "email": ("smtp_host", "smtp_port", "smtp_user", "smtp_password", "mail_from", "mail_to", "smtp_ssl"),
}

def normalize_push_channels(push: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {"pushplus", "telegram", "dingrobot", "feishubot", "email", "qq"}
    raw_channels = push.get("channels")
    if not isinstance(raw_channels, list):
        raw_channels = []

    channels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_channels:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip()
        if provider not in allowed or provider in seen:
            continue
        seen.add(provider)
        channel: dict[str, Any] = {
            "provider": provider,
            "enable": parse_bool(raw.get("enable", push.get("enable", False))),
        }
        for field in PUSH_CHANNEL_FIELDS[provider]:
            if field == "smtp_port":
                channel[field] = int(raw.get(field) or 465)
            elif field == "smtp_ssl":
                channel[field] = parse_bool(raw.get(field, True))
            else:
                channel[field] = str(raw.get(field) or "")
        if should_keep_push_channel(channel):
            channels.append(channel)
    return channels


def should_keep_push_channel(channel: dict[str, Any]) -> bool:
    if channel.get("enable"):
        return True
    provider = str(channel.get("provider") or "")
    for field in PUSH_CHANNEL_FIELDS.get(provider, ()):
        if field == "smtp_ssl":
            continue
        if field == "smtp_port":
            if int(channel.get(field) or 465) != 465:
                return True
            continue
        if str(channel.get(field) or "").strip():
            return True
    return False


def normalize_ip_guard(config: dict[str, Any]) -> None:
    """出口 IP 守卫：境外 IP 时暂停签到，等回到中国大陆再继续。"""
    guard = config.setdefault("ip_guard", {})
    if not isinstance(guard, dict):
        guard = {}
        config["ip_guard"] = guard
    guard["enable"] = parse_bool(guard.get("enable", True))
    try:
        interval = max(int(guard.get("check_interval") or 300), 30)
    except (TypeError, ValueError):
        interval = 300
    guard["check_interval"] = interval
    try:
        max_wait = max(int(guard.get("max_wait") or 0), 0)
    except (TypeError, ValueError):
        max_wait = 0
    guard["max_wait"] = max_wait
    guard["notify"] = parse_bool(guard.get("notify", True))
    on_error = str(guard.get("on_error") or "allow").strip().lower()
    guard["on_error"] = on_error if on_error in {"allow", "block"} else "allow"
    endpoints: list[str] = []
    raw = guard.get("endpoints") if isinstance(guard.get("endpoints"), list) else []
    for item in raw:
        url = str(item or "").strip()
        if url.startswith(("http://", "https://")) and url not in endpoints:
            endpoints.append(url)
        if len(endpoints) >= 5:
            break
    guard["endpoints"] = endpoints


def normalize_account_gap(config: dict[str, Any]) -> None:
    """账号之间的防风控随机等待。

    单位是分钟（网页里也是分钟，用户改起来直观）。range 取 [min, max] 闭区间内的
    随机值；min > max 时自动交换；上限夹在 24 小时以内——再长就会把第二个账号
    推到第二天的定时任务上去。
    """
    gap = config.setdefault("account_gap", {})
    if not isinstance(gap, dict):
        gap = {}
        config["account_gap"] = gap
    gap["enable"] = parse_bool(gap.get("enable", True))
    low = clamp_gap_minutes(gap.get("min_minutes"), 60)
    high = clamp_gap_minutes(gap.get("max_minutes"), 120)
    if high < low:
        low, high = high, low
    gap["min_minutes"] = low
    gap["max_minutes"] = high


def clamp_gap_minutes(value: Any, default: int) -> int:
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(minutes, 0), MAX_ACCOUNT_GAP_MINUTES)


def normalize_schedule(config: dict[str, Any]) -> None:
    """每日调度：把容易写错的写法拉回可用状态。

    最容易踩的坑：`time: 09:00` 不加引号时，PyYAML 按 YAML 1.1 的「六十进制」
    把它解析成整数 540（= 9×60 分钟）。原来自动调度会一路抛
    「schedule.time 必须是 HH:MM 格式」，把调度线程直接搞死——线程一死自动签到
    就永远不触发，而网页上还显示着「已启用」。这里把 0~1439 的整数还原成 HH:MM。
    """
    schedule = config.setdefault("schedule", {})
    if not isinstance(schedule, dict):
        schedule = {}
        config["schedule"] = schedule
    schedule["enable"] = parse_bool(schedule.get("enable", True))
    schedule["run_on_start"] = parse_bool(schedule.get("run_on_start", False))
    schedule["time"] = normalize_schedule_time(schedule.get("time"))
    schedule["jitter_minutes"] = clamp_minutes(schedule.get("jitter_minutes"), 45, 720)


def normalize_schedule_time(value: Any) -> str:
    """`09:00` / `"9:00"` / 540（YAML 六十进制）都还原成 "09:00"。"""
    default = str(DEFAULT_CONFIG["schedule"]["time"])
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        if 0 <= value < 24 * 60:
            return f"{value // 60:02d}:{value % 60:02d}"
        return default
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) == 2 and all(part.strip().isdigit() for part in parts):
        hour, minute = int(parts[0]), int(parts[1])
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    return default


def clamp_minutes(value: Any, default: int, upper: int) -> int:
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default
    return min(max(minutes, 0), upper)


def normalize_captcha_channels(captcha: dict[str, Any]) -> list[dict[str, Any]]:
    allowed = {"damagou", "local"}
    raw_channels = captcha.get("channels")
    if not isinstance(raw_channels, list):
        raw_channels = []
    if not raw_channels:
        raw_channels = [{"provider": "damagou", "enable": False}]

    channels: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_channels:
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider") or "").strip() or "damagou"
        if provider not in allowed or provider in seen:
            continue
        seen.add(provider)
        try:
            timeout = max(float(raw.get("timeout") or 60), 1)
            normalized_timeout: int | float = int(timeout) if timeout.is_integer() else timeout
        except (TypeError, ValueError):
            normalized_timeout = 60
        channel: dict[str, Any] = {
            "provider": provider,
            "enable": parse_bool(raw.get("enable", captcha.get("enable", False))),
            "userkey": str(raw.get("userkey") or ""),
            "type": str(raw.get("type") or ""),
            "timeout": normalized_timeout,
        }
        if provider == "local":
            channel["headless"] = parse_bool(raw.get("headless", True))
            try:
                channel["max_attempts"] = max(int(raw.get("max_attempts") or 5), 1)
            except (TypeError, ValueError):
                channel["max_attempts"] = 5
            channel["model_path"] = str(raw.get("model_path") or "")
        channels.append(channel)
    if not any(channel["provider"] == "damagou" for channel in channels):
        channels.append({"provider": "damagou", "enable": False, "userkey": "", "type": "", "timeout": 60})
    return channels


def parse_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def validate_unique_account_uids(config: dict[str, Any]) -> None:
    seen: dict[str, int] = {}
    for index, account in enumerate(config.get("accounts", []), start=1):
        uid = str(account.get("stuid") or "").strip()
        if not uid:
            continue
        if uid in seen:
            raise ValueError(f"UID {uid} 已存在于账号 {seen[uid]}，不能重复添加同一账号")
        seen[uid] = index


def find_account(config: dict[str, Any], name: str | None = None) -> dict[str, Any]:
    accounts = config.get("accounts") or []
    if not accounts:
        raise ValueError("配置中没有账号")
    if name is None:
        return accounts[0]
    for account in accounts:
        if account.get("name") == name:
            return account
    raise ValueError(f"未找到账号: {name}")


def upsert_account(config: dict[str, Any], name: str, data: dict[str, Any]) -> dict[str, Any]:
    accounts = config.setdefault("accounts", [])
    new_uid = str(data.get("stuid") or "").strip()
    if new_uid:
        for account in accounts:
            if account.get("name") != name and str(account.get("stuid") or "").strip() == new_uid:
                raise ValueError(f"UID {new_uid} 已存在，不能重复添加同一账号")
    for account in accounts:
        if account.get("name") == name:
            account.update(data)
            return account
    account = {"name": name, **data}
    accounts.append(account)
    return account


def load_credentials(config_path: pathlib.Path, config: dict[str, Any]) -> dict[str, Any]:
    path = credentials_path(config_path, config)
    if not path.exists():
        return {"accounts": []}
    with path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"凭证文件格式错误: {path}")
    loaded.setdefault("accounts", [])
    return loaded


def save_credentials(path: pathlib.Path, config: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    credentials = {
        "accounts": [
            {
                "name": str(account.get("name") or ""),
                **{field: str(account.get(field) or "") for field in SENSITIVE_ACCOUNT_FIELDS},
                "cloud_games": {
                    "tokens": normalize_cloud_game_tokens(
                        (account.get("cloud_games") or {}).get("tokens", {})
                        if isinstance(account.get("cloud_games"), dict)
                        else {}
                    )
                },
            }
            for index, account in enumerate(config.get("accounts", []), start=1)
        ]
    }
    with path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.safe_dump(credentials, file, allow_unicode=True, sort_keys=False)


def merge_credentials(config: dict[str, Any], credentials: dict[str, Any]) -> None:
    if not config.get("accounts") and credentials.get("accounts"):
        config["accounts"] = [
            {"name": str(account.get("name") or "")}
            for account in credentials.get("accounts", [])
            if isinstance(account, dict)
        ]
    credential_list = [
        account
        for account in credentials.get("accounts", [])
        if isinstance(account, dict)
    ]
    credential_accounts = {
        str(account.get("name")): account
        for account in credential_list
        if account.get("name")
    }
    for index, account in enumerate(config.get("accounts", [])):
        saved = credential_accounts.get(str(account.get("name")))
        if saved is None and index < len(credential_list):
            saved = credential_list[index]
        for field in SENSITIVE_ACCOUNT_FIELDS:
            if saved and not account.get(field):
                account[field] = str(saved.get(field) or "")
            else:
                account.setdefault(field, "")
        merge_account_cloud_game_credentials(account, saved)


def strip_credentials(config: dict[str, Any]) -> dict[str, Any]:
    public_config = copy.deepcopy(config)
    for account in public_config.get("accounts", []):
        for field in SENSITIVE_ACCOUNT_FIELDS:
            account.pop(field, None)
        account.pop("cloud_games", None)
    return public_config


def merge_account_cloud_game_credentials(account: dict[str, Any], saved: dict[str, Any] | None) -> None:
    if saved and isinstance(saved.get("cloud_games"), dict):
        saved_tokens = normalize_cloud_game_tokens((saved.get("cloud_games") or {}).get("tokens", {}))
    else:
        saved_tokens = {}
    cloud_games = account.setdefault("cloud_games", {})
    if not isinstance(cloud_games, dict):
        cloud_games = {}
        account["cloud_games"] = cloud_games
    cloud_games["tokens"] = saved_tokens


def credentials_path(config_path: str | pathlib.Path, config: dict[str, Any]) -> pathlib.Path:
    storage = config.get("storage", {})
    data_dir = resolve_storage_path(config_path, str(storage.get("data_dir") or "data"))
    return data_dir / str(storage.get("credentials_file") or "credentials.yaml")


def log_path(config_path: str | pathlib.Path, config: dict[str, Any]) -> pathlib.Path:
    storage = config.get("storage", {})
    log_dir = resolve_storage_path(config_path, str(storage.get("log_dir") or "logs"))
    return log_dir / str(storage.get("log_file") or "miyouqian.log")


def resolve_storage_path(config_path: str | pathlib.Path, value: str) -> pathlib.Path:
    path = pathlib.Path(value)
    if path.is_absolute():
        return path
    return pathlib.Path(config_path).parent / path
