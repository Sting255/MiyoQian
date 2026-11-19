# -*- coding: utf-8 -*-
"""验证码识别渠道。"""

from __future__ import annotations

import gc
import json
import pathlib
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable
from uuid import uuid4

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
_DEBUG_LOCK = threading.Lock()
_DEBUG_KEEP_SESSIONS = 20
_DEBUG_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DEBUG_KIND = "miyouqian.captcha-debug"
_DEBUG_SESSION_NAME = re.compile(r"session-\d{8}T\d{12}Z-[0-9a-f]{12}\Z")
_DEBUG_ARTIFACT_NAME = re.compile(r"attempt\d+-\d{6}(?:-grid\.png|-icon\.png|\.json)\Z")


def _solve_local(
    channel: dict[str, Any],
    config: dict[str, Any],
    gt: str,
    challenge: str,
    emit: Callable[[str], None] | None,
) -> CaptchaSolution | None:
    """用本机 Chrome + 视觉模型在本地解极验九宫格，不依赖第三方打码服务。"""
    debug_dir: pathlib.Path | None = None
    succeeded = False
    error_type = ""
    counts = {key: 0 for key in (
        "callback_timeout", "image_fetch_error", "attempt_error", "panel_missing", "callback_not_passed",
    )}

    def log(message: str) -> None:
        # nine.py 的这条文案只出现在 wait_for(timeout=12) 超时分支，
        # 并不是服务端明确判错。只校正文案，不改变点选、等待或重试行为。
        if re.fullmatch(r"第 \d+ 次点选未通过：答案被拒绝", message):
            counts["callback_timeout"] += 1
            message = message.removesuffix("答案被拒绝") + "12 秒内未收到成功或错误回调（超时，不能认定答案错误）"
        elif "九宫格图片获取失败:" in message:
            counts["image_fetch_error"] += 1
        elif re.match(r"第 \d+ 次尝试异常：", message):
            counts["attempt_error"] += 1
        elif re.match(r"第 \d+ 次未取到九宫格结构", message):
            counts["panel_missing"] += 1
        elif re.match(r"第 \d+ 次点选未通过：", message):
            counts["callback_not_passed"] += 1
        if emit:
            emit(message)

    try:
        from .geetest import nine

        debug_dir = _prepare_debug_session(_debug_dir(config), log)
        matcher = _local_matcher(channel, log)
        validate, passed_challenge = nine.solve(
            gt,
            challenge,
            matcher=matcher,
            emit=log,
            headless=bool(channel.get("headless", True)),
            max_attempts=int(channel.get("max_attempts") or 5),
            debug_dir=debug_dir,
        )
        succeeded = True
        return CaptchaSolution(validate=validate, challenge=passed_challenge)
    except Exception as exc:
        error_type = type(exc).__name__
        if emit:
            emit(f"本地验证码识别失败: {exc}")
        return None
    finally:
        if debug_dir is not None:
            _finish_debug_session(debug_dir, succeeded, error_type, counts, log)
        if emit and any(counts.values()):
            emit(
                "本地验证码诊断："
                f"回调超时 {counts['callback_timeout']} 次，"
                f"图片获取失败 {counts['image_fetch_error']} 次，"
                f"其他尝试异常 {counts['attempt_error']} 次，"
                f"面板缺失 {counts['panel_missing']} 次，"
                f"回调返回未通过 {counts['callback_not_passed']} 次"
            )


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


def _write_debug_summary(folder: pathlib.Path, summary: dict[str, Any]) -> None:
    temporary = folder / "summary.json.tmp"
    temporary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(folder / "summary.json")


def _prepare_debug_session(
    root: pathlib.Path, emit: Callable[[str], None],
) -> pathlib.Path | None:
    """每次外层求解独立存图；诊断目录不可写时仍继续识别。"""
    try:
        now = datetime.now(timezone.utc)
        name = f"session-{now:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:12]}"
        with _DEBUG_LOCK:
            root.mkdir(parents=True, exist_ok=True)
            folder = root / name
            folder.mkdir()
            _write_debug_summary(folder, {
                "kind": _DEBUG_KIND, "version": 1, "status": "running", "started_at": now.isoformat(),
            })
    except OSError:
        emit("验证码现场目录不可写（不影响识别，无法保存本轮现场）")
        return None
    emit(f"验证码现场编号：{name}")
    return folder


def _finish_debug_session(
    folder: pathlib.Path, succeeded: bool, error_type: str,
    counts: dict[str, int], emit: Callable[[str], None],
) -> None:
    try:
        with _DEBUG_LOCK:
            summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
            if not isinstance(summary, dict):
                raise ValueError("invalid debug summary")
            summary.update(
                status="success" if succeeded else "failed",
                finished_at=datetime.now(timezone.utc).isoformat(),
                counts=dict(counts), error_type=error_type,
            )
            # 不保存 gt、challenge、validate、URL、账号或原始异常消息。
            _write_debug_summary(folder, summary)
            _prune_debug_sessions(folder.parent)
    except (OSError, ValueError):
        emit("验证码现场摘要或历史清理失败（不影响识别）")


def _prune_debug_sessions(root: pathlib.Path) -> None:
    """仅清理本模块已完成的现场；运行中的目录、旧文件、用户文件均保留。"""
    completed: list[tuple[float, pathlib.Path]] = []
    resolved_root = root.resolve()
    for folder in root.iterdir():
        if not _DEBUG_SESSION_NAME.fullmatch(folder.name):
            continue
        try:
            # 不跟随符号链接或 Windows junction 越过现场根目录。
            if folder.is_symlink() or not folder.is_dir() or folder.resolve() != resolved_root / folder.name:
                continue
            manifest = folder / "summary.json"
            if manifest.is_symlink() or manifest.stat().st_size > 16_384:
                continue
            summary = json.loads(manifest.read_text(encoding="utf-8"))
            if not isinstance(summary, dict) or summary.get("kind") != _DEBUG_KIND or summary.get("version") != 1:
                continue
            if summary.get("status") not in ("success", "failed"):
                continue
            finished = datetime.fromisoformat(str(summary.get("finished_at") or ""))
            if finished.tzinfo is None:
                continue
            completed.append((finished.timestamp(), folder))
        except (OSError, ValueError, OverflowError):
            continue
    completed.sort(key=lambda entry: (entry[0], entry[1].name), reverse=True)
    now = datetime.now(timezone.utc).timestamp()
    for index, (finished_at, folder) in enumerate(completed):
        if index < _DEBUG_KEEP_SESSIONS and now - finished_at <= _DEBUG_MAX_AGE_SECONDS:
            continue
        try:
            # 不递归删除；混入用户文件或子目录的现场留给人工处理。
            files = list(folder.iterdir())
            if any(item.is_symlink() or not item.is_file() or (
                item.name != "summary.json" and not _DEBUG_ARTIFACT_NAME.fullmatch(item.name)
            ) for item in files):
                continue
            for item in files:
                item.unlink()
            folder.rmdir()
        except OSError:
            continue


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
