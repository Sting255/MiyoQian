# -*- coding: utf-8 -*-
"""验证码识别渠道。"""

from __future__ import annotations

import gc
import pathlib
import threading
from dataclasses import dataclass
from typing import Any, Callable

from .http import ApiClient

DAMAGOU_URL = "http://api.damagou.top/apiv1/jiyanRecognize.html"
PROVIDER_LABELS = {
    "damagou": "打码狗",
    "local": "本地识别",
}
# 需要额外配置 userkey 的渠道
KEY_REQUIRED_PROVIDERS = ("damagou",)


@dataclass(frozen=True)
class CaptchaSolution:
    validate: str
    challenge: str


def is_enabled(config: dict[str, Any]) -> bool:
    return _active_channel(config) is not None


def active_provider_label(config: dict[str, Any]) -> str:
    channel = _active_channel(config)
    if not channel:
        return "验证码渠道"
    provider = str(channel.get("provider") or "")
    return PROVIDER_LABELS.get(provider, provider or "验证码渠道")


def solve_game_captcha(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    emit: Callable[[str], None] | None = None,
) -> CaptchaSolution | None:
    return _solve(client, config, gt, challenge, None, emit)


def solve_bbs_captcha(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None = None,
    emit: Callable[[str], None] | None = None,
) -> CaptchaSolution | None:
    return _solve(client, config, gt, challenge, geetest_success, emit)


def _solve(
    client: ApiClient,
    config: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    channel = _active_channel(config)
    if not channel:
        return None
    provider = str(channel.get("provider") or "damagou")
    if provider == "local":
        return _solve_local(channel, config, gt, challenge, emit)
    return _solve_damagou(client, config, channel, gt, challenge, geetest_success, emit)


_LOCAL_MATCHER: Any = None
# 保护上面那个全局单例：加载/释放都走它，避免重叠任务各加载一份 139MB 的模型
_MATCHER_LOCK = threading.Lock()


def _solve_local(
    channel: dict[str, Any],
    config: dict[str, Any],
    gt: str,
    challenge: str,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    """用本机 Chrome + 视觉模型在本地解极验九宫格，不依赖第三方打码服务。"""
    try:
        from .geetest import nine

        matcher = _local_matcher(channel, emit)
        debug_dir = _debug_dir(config)
        # 只保留这一次求解的现场，避免 data/captcha_debug/ 越积越多
        # （只删本模块自己生成的 attempt*-grid.png / -icon.png / *.json）
        nine.reset_debug_dir(debug_dir)
        validate, passed_challenge = nine.solve(
            gt,
            challenge,
            matcher=matcher,
            emit=emit,
            headless=bool(channel.get("headless", True)),
            max_attempts=int(channel.get("max_attempts") or 5),
            debug_dir=debug_dir,
        )
        return CaptchaSolution(validate=validate, challenge=passed_challenge)
    except Exception as exc:
        if emit:
            emit(f"本地验证码识别失败: {exc}")
        return None


def _debug_dir(config: dict[str, Any]) -> pathlib.Path:
    """识别失败时的现场图（合成图 + 图标 + 分数）落在这里。

    相对路径按**仓库根**解析，而不是当前工作目录：服务被从别的目录启动时
    （计划任务、容器、IDE），现场图不该跑到别处，更不该因为那个目录只读而白失败一次。
    """
    from .geetest import nine

    repo_data = nine.default_models_dir().parent
    storage = config.get("storage", {}) if isinstance(config, dict) else {}
    raw = str(storage.get("data_dir") or "").strip()
    if not raw:
        return repo_data / "captcha_debug"
    base = pathlib.Path(raw)
    if base.is_absolute():
        return base / "captcha_debug"
    return repo_data.parent / base / "captcha_debug"


def _local_matcher(channel: dict[str, Any], emit: Callable[[str], None] | None) -> Any:
    global _LOCAL_MATCHER
    # 加锁：模型约 139MB，万一两个任务重叠，没锁会各加载一份白吃内存
    with _MATCHER_LOCK:
        if _LOCAL_MATCHER is None:
            from .geetest import nine

            model_path = str(channel.get("model_path") or "").strip() or nine.model_path()
            path = nine.ensure_model(model_path, emit=emit)
            if emit:
                emit("正在加载本地识别模型")
            _LOCAL_MATCHER = nine.TileMatcher(path)
        return _LOCAL_MATCHER


def release_matcher() -> None:
    """释放常驻的视觉识别模型，把内存还给系统。

    模型常驻约 139MB，而加载只要约 0.5 秒，所以任务跑完就放掉更划算；
    下次再遇到验证码会自动重新加载。
    """
    global _LOCAL_MATCHER
    with _MATCHER_LOCK:
        if _LOCAL_MATCHER is None:
            return
        _LOCAL_MATCHER = None
    gc.collect()


def _solve_damagou(
    client: ApiClient,
    config: dict[str, Any],
    channel: dict[str, Any],
    gt: str,
    challenge: str,
    geetest_success: int | None,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    try:
        params: dict[str, Any] = {
            "userkey": str(channel.get("userkey") or "").strip(),
            "gt": gt,
            "challenge": challenge,
            "isJson": "2",
        }
        captcha_type = str(channel.get("type") or "").strip()
        if captcha_type:
            params["type"] = captcha_type
        if geetest_success == 0:
            params["success"] = 0
        data = client.get_json(
            DAMAGOU_URL, params=params, timeout=float(channel.get("timeout") or 60)
        )
        solution = _parse_damagou_response(data)
        if not solution and emit:
            emit(
                f"{active_provider_label(config)}验证码识别失败: "
                f"{data.get('msg') or '未知错误'}"
            )
        return solution
    except Exception as exc:
        if emit:
            emit(f"{active_provider_label(config)}验证码识别失败: {exc}")
        return None


def _active_channel(config: dict[str, Any]) -> dict[str, Any] | None:
    captcha_config = config.get("captcha", {})
    if not captcha_config.get("enable"):
        return None
    channels = captcha_config.get("channels") or []
    if not isinstance(channels, list):
        return None
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        if not channel.get("enable"):
            continue
        provider = str(channel.get("provider") or "")
        if provider not in PROVIDER_LABELS:
            continue
        if provider in KEY_REQUIRED_PROVIDERS and not str(channel.get("userkey") or "").strip():
            continue
        return channel
    return None


def _parse_damagou_response(data: dict[str, Any]) -> CaptchaSolution | None:
    if str(data.get("status")) != "0":
        return None
    raw = str(data.get("data") or "")
    if "|" not in raw:
        return None
    challenge, validate = raw.split("|", 1)
    challenge = challenge.strip()
    validate = validate.strip()
    if not challenge or not validate:
        return None
    return CaptchaSolution(validate=validate, challenge=challenge)
