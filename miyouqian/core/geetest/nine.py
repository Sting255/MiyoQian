# -*- coding: utf-8 -*-
"""极验三代「九宫格点选」本地求解器。

验证码由极验自己的 JS 在本地 Chrome 中渲染与校验（行为数据天然真实），
我们只做两件事：用视觉模型判断该点哪几格，然后用鼠标点上去。
"""

from __future__ import annotations

import io
import json
import pathlib
import time
import urllib.parse
import urllib.request
from typing import Any, Callable

import numpy as np
from PIL import Image

from .browser import Browser, find_browser

MODEL_FILENAME = "clip_vision_quant.onnx"
MODEL_URL = (
    "https://hf-mirror.com/Xenova/clip-vit-base-patch32/resolve/main/"
    "onnx/vision_model_quantized.onnx"
)
MODEL_MIN_BYTES = 50 * 1024 * 1024
# 下载兜底：单次 socket 超时管不住「服务器一直慢慢滴」，总时长和总大小都要有上限
MODEL_DOWNLOAD_TIMEOUT = 600
MODEL_MAX_BYTES = 200 * 1024 * 1024
IMAGE_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

# 相似度判定：从上往下找第一个明显断层作为切分点，断层不可信时退回绝对阈值
MIN_SIMILARITY = 0.70
GAP_THRESHOLD = 0.06
MAX_TILES = 5

# 九宫格合成图：上方 344×344 是 3×3 图片，底部 40 像素是提示图标条
COMPOSITE_SIZE = (344, 384)
GRID_EDGE = 344
ICON_BOX = (0, 344, 118, 384)

PANEL_JS = r"""
(function(){
  function rect(el){var r=el.getBoundingClientRect();
    return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
  var img=document.querySelector('.geetest_item_img');
  var items=Array.from(document.querySelectorAll('.geetest_item_wrap')).map(rect);
  var confirmBtn=document.querySelector('.geetest_commit');
  if(!img || items.length !== 9){return '';}
  return JSON.stringify({
    imgSrc: img.src || '',
    items: items,
    confirm: confirmBtn? rect(confirmBtn) : null
  });
})()
"""


class ModelUnavailable(RuntimeError):
    """视觉模型缺失。"""


def preprocess(image: Image.Image) -> np.ndarray:
    resized = image.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE), Image.BICUBIC)
    array = np.asarray(resized, dtype=np.float32) / 255.0
    array = (array - CLIP_MEAN) / CLIP_STD
    return array.transpose(2, 0, 1)


class TileMatcher:
    """用 CLIP 视觉编码器比较提示图标与九个格子。"""

    def __init__(self, model_path: str | pathlib.Path) -> None:
        import onnxruntime as ort

        path = pathlib.Path(model_path)
        if not path.exists():
            raise ModelUnavailable(f"缺少视觉模型: {path}")
        self.session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name

    def similarities(self, icon: Image.Image, tiles: list[Image.Image]) -> list[float]:
        batch = np.stack([preprocess(item) for item in [icon, *tiles]])
        vectors = self.session.run(None, {self.input_name: batch})[0]
        icon_vector = vectors[0]
        icon_norm = icon_vector / (np.linalg.norm(icon_vector) + 1e-8)
        scores: list[float] = []
        for vector in vectors[1:]:
            unit = vector / (np.linalg.norm(vector) + 1e-8)
            scores.append(float(np.dot(icon_norm, unit)))
        return scores

    def pick(self, icon: Image.Image, tiles: list[Image.Image]) -> tuple[list[int], list[float]]:
        """相似度降序排列，取第一个明显断层作为切分点。"""
        scores = self.similarities(icon, tiles)
        order = sorted(range(len(scores)), key=lambda item: scores[item], reverse=True)
        ranked = [scores[index] for index in order]

        cut = 0
        for index in range(len(ranked) - 1):
            if ranked[index] - ranked[index + 1] >= GAP_THRESHOLD:
                cut = index + 1
                break

        if 0 < cut <= MAX_TILES:
            selected = sorted(order[:cut])
        else:
            selected = [
                index for index, score in enumerate(scores) if score >= MIN_SIMILARITY
            ]
            if not selected:
                selected = [order[0]]
            elif len(selected) > MAX_TILES:
                # 分数过于分散，只取最像的一个，避免大面积误选
                selected = [order[0]]
        return selected, scores


def split_composite(picture: Image.Image) -> tuple[Image.Image, list[Image.Image]]:
    """把九宫格合成图切成 (提示图标, 九个格子)。"""
    image = picture.convert("RGB")
    scale_x = image.width / COMPOSITE_SIZE[0]
    scale_y = image.height / COMPOSITE_SIZE[1]
    edge = GRID_EDGE * min(scale_x, scale_y)
    step = edge / 3
    tiles: list[Image.Image] = []
    for row in range(3):
        for column in range(3):
            left = round(column * step)
            top = round(row * step)
            tiles.append(image.crop((left, top, round(left + step), round(top + step))))
    box = (
        round(ICON_BOX[0] * scale_x),
        round(ICON_BOX[1] * scale_y),
        round(ICON_BOX[2] * scale_x),
        round(ICON_BOX[3] * scale_y),
    )
    icon = image.crop(box)
    return trim(icon), tiles


def trim(image: Image.Image) -> Image.Image:
    """去掉四周白边，让图标尽量占满，便于模型比较。"""
    gray = image.convert("L")
    mask = gray.point(lambda value: 255 if value < 235 else 0)
    box = mask.getbbox()
    if not box:
        return image
    pad = 2
    box = (
        max(0, box[0] - pad),
        max(0, box[1] - pad),
        min(image.width, box[2] + pad),
        min(image.height, box[3] + pad),
    )
    return image.crop(box)


def repo_root() -> pathlib.Path:
    """仓库根目录（用本文件位置推导，不看当前工作目录）。"""
    return pathlib.Path(__file__).resolve().parents[3]


def default_models_dir() -> pathlib.Path:
    """模型默认目录：仓库根的 `data/models`。

    用文件位置推导而不是 CWD：否则换个工作目录（计划任务、容器、IDE）启动就会
    以为模型丢了，重新下载 85MB。验证码现场图的目录也用它的父目录做基准。
    """
    return repo_root() / "data" / "models"


def model_path(data_dir: str | pathlib.Path | None = None) -> pathlib.Path:
    """模型文件路径；传了 data_dir 就按它算（视为模型所在目录）。"""
    if data_dir:
        return pathlib.Path(data_dir) / MODEL_FILENAME
    return default_models_dir() / MODEL_FILENAME


def _validate_download_url(url: str) -> str:
    """下载地址只允许模块内写死的那个镜像域名的 https 链接。

    这个参数历史上只用于测试注入，但既然是服务端发请求，就不能让
    任意 URL 原样通过（SSRF），不合法的地址按缺模型处理。
    """
    allowed_host = urllib.parse.urlparse(MODEL_URL).hostname
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != allowed_host:
        raise ModelUnavailable(f"不允许的模型下载地址: {url}")
    return url


def ensure_model(
    path: str | pathlib.Path,
    emit: Callable[[str], None] | None = None,
    url: str = MODEL_URL,
) -> pathlib.Path:
    """模型缺失时自动下载（约 85MB，走国内镜像）。"""
    target = pathlib.Path(path)
    if target.exists() and target.stat().st_size >= MODEL_MIN_BYTES:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    if emit:
        emit(f"首次使用本地识别，正在下载视觉模型（约 85MB）到 {target}")
    temp = target.with_suffix(target.suffix + ".part")
    try:
        request = urllib.request.Request(
            _validate_download_url(url), headers={"user-agent": "myq/1.0"}
        )
        deadline = time.time() + MODEL_DOWNLOAD_TIMEOUT
        received = 0
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                received += len(chunk)
                if received > MODEL_MAX_BYTES:
                    raise ModelUnavailable(
                        f"视觉模型下载超出大小上限（>{MODEL_MAX_BYTES // (1024 * 1024)}MB）"
                    )
                if time.time() > deadline:
                    raise ModelUnavailable(
                        f"视觉模型下载超时（>{MODEL_DOWNLOAD_TIMEOUT} 秒），请稍后重试"
                    )
                handle.write(chunk)
        if received < MODEL_MIN_BYTES:
            raise ModelUnavailable("视觉模型下载不完整，请稍后重试")
    except BaseException:
        # 中途失败（网络断 / 超时 / 超限）不留半成品，下次从头再来
        temp.unlink(missing_ok=True)
        raise
    temp.replace(target)
    if emit:
        emit("视觉模型下载完成")
    return target


def save_debug_artifacts(
    debug_dir: str | pathlib.Path,
    picture: "Image.Image",
    icon: "Image.Image",
    selected: list[int],
    scores: list[float],
    attempt: int,
    log: Callable[[str], None],
) -> None:
    """把这次识别的现场存下来（合成图 + 放大的提示图标 + 分数），方便事后复盘。

    落盘失败（目录只读、磁盘满、容器里没有可写目录…）**绝不能影响识别本身**，
    所以整体吞掉异常，只记一行日志。
    """
    try:
        folder = pathlib.Path(debug_dir)
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%H%M%S")
        picture.save(folder / f"attempt{attempt}-{stamp}-grid.png")
        icon.resize((icon.width * 4, icon.height * 4), Image.LANCZOS).save(
            folder / f"attempt{attempt}-{stamp}-icon.png"
        )
        (folder / f"attempt{attempt}-{stamp}.json").write_text(
            json.dumps(
                {"selected": selected, "scores": scores, "attempt": attempt},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception as exc:
        log(f"现场图保存失败（不影响识别）: {exc}")


def fetch_image(url: str, attempts: int = 3) -> Image.Image:
    if not url:
        raise RuntimeError("缺少九宫格图片地址")
    last_error: Exception | None = None
    for index in range(attempts):
        target = url
        if index:
            separator = "&" if "?" in url else "?"
            target = f"{url}{separator}_r={int(time.time() * 1000)}"
        try:
            request = urllib.request.Request(
                target,
                headers={
                    "user-agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
                    ),
                    "referer": "https://static.geetest.com/",
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = response.read()
            return Image.open(io.BytesIO(payload)).convert("RGB")
        except Exception as exc:  # 图片偶发拉取失败，重试
            last_error = exc
            time.sleep(0.8)
    raise RuntimeError(f"九宫格图片获取失败: {last_error}")


def reset_debug_dir(folder: str | pathlib.Path) -> pathlib.Path:
    """清掉上一次识别失败留下的现场文件（grid/icon/json）。

    只删本模块命名的这三种，目录里的其它东西（比如用户自己放的笔记）不动。
    """
    path = pathlib.Path(folder)
    if not path.exists():
        return path
    for item in path.iterdir():
        name = item.name
        ours = (
            name.startswith("attempt")
            and (name.endswith("-grid.png") or name.endswith("-icon.png") or name.endswith(".json"))
        )
        if not ours:
            continue
        try:
            item.unlink()
        except OSError:
            continue
    return path


def self_check(headless: bool = True) -> dict[str, Any]:
    """在服务环境里真拉起一次 Chrome 并打开 harness 页面。

    目的：不等 9 点签到失败才发现环境问题。检查链路 =
    找浏览器 → 起进程 → 连调试端口 → 起本地 harness 服务 → 打开页面。
    不加载极验、不下载模型、不碰任何账号。
    返回 ok=True 表示整条链路可用；失败时把浏览器 stderr 带出来。
    """
    started = time.time()
    result: dict[str, Any] = {"ok": False, "browser": "", "seconds": 0.0}
    try:
        result["browser"] = find_browser()
    except RuntimeError as exc:
        result["error"] = str(exc)
        return result

    try:
        browser = Browser(headless=headless)
    except Exception as exc:
        result["error"] = f"浏览器初始化失败: {exc}"
        result["seconds"] = round(time.time() - started, 1)
        return result
    try:
        page = browser.start()
        browser.open_harness("selfcheck", "selfcheck")
        # harness 是本地静态页，加载完成即可；再取一次页面标题确认能执行 JS
        title = page.evaluate("document.title || ''")
        result.update(ok=True, seconds=round(time.time() - started, 1))
        if title:
            result["title"] = str(title)
        return result
    except Exception as exc:
        result["error"] = str(exc)
        result["seconds"] = round(time.time() - started, 1)
        return result
    finally:
        browser.close()


def solve(
    gt: str,
    challenge: str,
    *,
    matcher: TileMatcher,
    emit: Callable[[str], None] | None = None,
    headless: bool = True,
    max_attempts: int = 5,
    debug_dir: str | pathlib.Path | None = None,
) -> tuple[str, str]:
    """返回 (geetest_validate, geetest_challenge)。"""

    def log(message: str) -> None:
        if emit:
            emit(message)

    def has_panel() -> bool:
        return bool(page.evaluate("document.querySelector('.geetest_item_img') ? 1 : 0"))

    def open_panel(refresh: bool) -> None:
        if refresh:
            page.evaluate(
                "(function(){var el=document.querySelector('.geetest_refresh');"
                "if(el){el.click();}})()"
            )
            time.sleep(1.5)
        if has_panel():
            return
        button = json.loads(
            page.evaluate(
                "(function(){var el=document.querySelector('.geetest_radar_btn')"
                "||document.querySelector('.geetest_btn');"
                "if(!el){return 'null';}"
                "var r=el.getBoundingClientRect();"
                "return JSON.stringify({x:r.x+r.width/2,y:r.y+r.height/2});})()"
            )
        )
        if not button:
            raise RuntimeError("找不到验证码入口按钮")
        page.click(button["x"], button["y"])
        page.wait_for("document.querySelector('.geetest_item_img') ? 1 : 0", timeout=20)

    browser = Browser(headless=headless)
    try:
        page = browser.start()
        browser.open_harness(gt, challenge)
        page.wait_for("window.__gt && window.__gt.status === 'ready'", timeout=35)
        log("验证码组件已就绪")

        for attempt in range(1, max_attempts + 1):
            try:
                open_panel(refresh=attempt > 1)
                time.sleep(1.2)

                raw = page.evaluate(PANEL_JS)
                if not raw:
                    log(f"第 {attempt} 次未取到九宫格结构")
                    continue
                meta = json.loads(raw)
                picture = fetch_image(str(meta.get("imgSrc") or ""))
                icon, tiles = split_composite(picture)

                selected, scores = matcher.pick(icon, tiles)
                log("相似度：" + " ".join(f"{score:.3f}" for score in scores))
                log(
                    "识别结果：格子 "
                    + "、".join(str(index + 1) for index in selected)
                    + f"（最高 {max(scores):.3f}）"
                )
                if debug_dir:
                    save_debug_artifacts(debug_dir, picture, icon, selected, scores, attempt, log)

                for index in selected:
                    box = meta["items"][index]
                    page.click(box["x"] + box["w"] / 2, box["y"] + box["h"] / 2)
                time.sleep(0.5)
                confirm = meta["confirm"]
                page.click(confirm["x"] + confirm["w"] / 2, confirm["y"] + confirm["h"] / 2)

                try:
                    state = page.wait_for(
                        "window.__gt.status === 'success' || window.__gt.status === 'error' "
                        "? JSON.stringify(window.__gt) : null",
                        timeout=12,
                    )
                except TimeoutError:
                    state = None
                if state:
                    payload = json.loads(state)
                    result = payload.get("result") or {}
                    validate = str(result.get("geetest_validate") or "")
                    if validate:
                        return validate, str(result.get("geetest_challenge") or challenge)
                    log(f"第 {attempt} 次点选未通过：{payload.get('message') or '答案错误'}")
                else:
                    log(f"第 {attempt} 次点选未通过：答案被拒绝")
            except Exception as exc:
                log(f"第 {attempt} 次尝试异常：{exc}")
            page.evaluate("window.__gt.status = 'ready'; window.__gt.result = null;")
        raise RuntimeError("九宫格验证码多次尝试未通过")
    finally:
        browser.close()
