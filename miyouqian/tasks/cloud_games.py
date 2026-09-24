# -*- coding: utf-8 -*-
"""国内云游戏每日签到。"""

from __future__ import annotations

import random
import time
from typing import Any, Callable

from .. import constants as c
from ..core.http import ApiClient


class CloudGameCheckin:
    def __init__(
        self,
        client: ApiClient,
        config: dict[str, Any],
        account: dict[str, Any],
        emit: Callable[[str], None] | None = None,
    ) -> None:
        self.client = client
        self.config = config
        self.account = account
        self.emit = emit

    def run(self) -> list[str]:
        cloud_games = self.account.get("cloud_games", {})
        if not isinstance(cloud_games, dict):
            cloud_games = {}
        tokens = cloud_games.get("tokens", {})
        if not isinstance(tokens, dict):
            tokens = {}
        enabled_games = self.config.get("cloud_games", {}).get("enabled", [])
        if not isinstance(enabled_games, list):
            enabled_games = list(c.CLOUD_GAMES)
        enabled_games = [
            game_key
            for game_key in enabled_games
            if game_key in c.CLOUD_GAMES
        ]
        configured_games = [
            game_key
            for game_key in enabled_games
            if str(tokens.get(game_key) or "").strip()
        ]
        messages: list[str] = []
        success: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []

        self._add(messages, "== 云游戏签到 ==")
        if not enabled_games:
            skipped.append("未启用具体云游戏")
            self._add(messages, "云游戏总开关已开启，但未选择具体云游戏，已跳过")
            self._add(messages, f"云游戏签到汇总：成功 {len(success)}，失败 {len(failed)}，跳过 {len(skipped)}")
            return messages
        if not configured_games:
            skipped.append("已启用云游戏未配置账号 token")
            enabled_names = "、".join(str(c.CLOUD_GAMES[game_key]["name"]) for game_key in enabled_games)
            self._add(messages, f"本账号未配置已启用云游戏的 x-rpc-combo_token（{enabled_names}），已跳过")
            self._add(messages, f"云游戏签到汇总：成功 {len(success)}，失败 {len(failed)}，跳过 {len(skipped)}")
            return messages

        for game_key in configured_games:
            game = c.CLOUD_GAMES.get(game_key)
            if not game:
                skipped.append(game_key)
                self._add(messages, f"[跳过] 未知云游戏配置: {game_key}")
                continue
            token = str(tokens.get(game_key) or "").strip()
            result = self._run_game(game, token)
            messages.extend(result["messages"])
            success.extend(result["success"])
            failed.extend(result["failed"])
            skipped.extend(result["skipped"])
            sleep_by_config(self.config)

        self._add(messages, f"云游戏签到汇总：成功 {len(success)}，失败 {len(failed)}，跳过 {len(skipped)}")
        if success:
            self._add(messages, f"云游戏成功项：{'; '.join(success)}")
        if failed:
            self._add(messages, f"云游戏失败项：{'; '.join(failed)}")
        return messages

    def _run_game(self, game: dict[str, Any], token: str) -> dict[str, list[str]]:
        messages: list[str] = []
        success: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        name = str(game["name"])
        self._add(messages, f"正在进行{name}签到")
        try:
            data = self._request_wallet(game, token)
        except Exception as exc:
            reason = f"请求异常: {exc}"
            self._add(messages, f"{name} 签到失败: {reason}")
            failed.append(f"{name} {reason}")
            return {"messages": messages, "success": success, "failed": failed, "skipped": skipped}

        retcode = data.get("retcode")
        if retcode == -100:
            reason = "token 失效或账号状态受限"
            self._add(messages, f"{name} 签到失败: {reason}")
            failed.append(f"{name} {reason}")
            return {"messages": messages, "success": success, "failed": failed, "skipped": skipped}
        if retcode != 0:
            reason = f"{data.get('message')}({retcode})"
            self._add(messages, f"{name} 签到失败: {reason}")
            failed.append(f"{name} {reason}")
            return {"messages": messages, "success": success, "failed": failed, "skipped": skipped}

        wallet = data.get("data") or {}
        free_time = free_time_minutes(wallet)
        send_free_time = send_free_time_minutes(wallet)
        gained = send_free_time
        if gained <= 0 and free_time < 600:
            gained, latest_wallet = self._retry_detect_gained_time(game, token, free_time)
            if latest_wallet is not None:
                wallet = latest_wallet
        elif gained > 0:
            # 签到响应里的 free_time 是「发放前」的快照，展示时要补上本次到账的时长
            free_time_info = dict(wallet.get("free_time") or {})
            free_time_info["free_time"] = str(free_time + gained)
            wallet = {**wallet, "free_time": free_time_info}

        if gained > 0:
            self._add(messages, f"{name} 签到成功，获得 {gained} 分钟免费时长")
            success.append(f"{name} 获得 {gained} 分钟免费时长")
        else:
            self._add(messages, f"{name} 今日已签到或免费时长已达上限")
            success.append(f"{name} 已签到或已达上限")
        self._add(messages, describe_wallet(game, wallet))
        return {"messages": messages, "success": success, "failed": failed, "skipped": skipped}

    def _request_wallet(self, game: dict[str, Any], token: str) -> dict[str, Any]:
        return self.client.get_json(
            str(game["sign_url"]),
            headers=cloud_headers(game, token),
        )

    def _retry_detect_gained_time(
        self, game: dict[str, Any], token: str, initial_free_time: int
    ) -> tuple[int, dict[str, Any] | None]:
        """延时二次查询确认本次签到获得的时长，并带回最新钱包数据。

        返回 (获得分钟数, 最新钱包)；查询异常或失败时返回 (0, None)，
        由调用方继续使用首次查询的钱包数据。
        """
        time.sleep(random.randint(3, 6))
        try:
            data = self._request_wallet(game, token)
        except Exception:
            return 0, None
        if data.get("retcode") != 0:
            return 0, None
        latest_wallet = data.get("data") or {}
        next_free_time = free_time_minutes(latest_wallet)
        return max(next_free_time - initial_free_time, 0), latest_wallet

    def _add(self, messages: list[str], message: str) -> None:
        messages.append(message)
        if self.emit:
            self.emit(message)


def cloud_headers(game: dict[str, Any], token: str) -> dict[str, str]:
    headers = {
        "Host": str(game["host"]),
        "Accept": "*/*",
        "x-rpc-combo_token": token,
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/99.0.4844.84 Safari/537.36"
        ),
    }
    referer = str(game.get("referer") or "")
    if referer:
        headers["Referer"] = referer
    return headers


def free_time_minutes(wallet: dict[str, Any]) -> int:
    return safe_int((wallet.get("free_time") or {}).get("free_time"))


def send_free_time_minutes(wallet: dict[str, Any]) -> int:
    return safe_int((wallet.get("free_time") or {}).get("send_freetime"))


def describe_wallet(game: dict[str, Any], wallet: dict[str, Any]) -> str:
    free_time = free_time_minutes(wallet)
    play_card = str((wallet.get("play_card") or {}).get("short_msg") or "未知")
    coin_num = safe_int((wallet.get("coin") or {}).get("coin_num"))
    return (
        f"{game['name']} 当前免费时长 {format_minutes(free_time)}，"
        f"畅玩卡状态 {play_card}，拥有{game['coin_name']} {coin_num} 枚"
    )


def format_minutes(minutes: int) -> str:
    minutes = max(minutes, 0)
    hours, minute = divmod(minutes, 60)
    if hours and minute:
        return f"{hours}小时{minute}分钟"
    if hours:
        return f"{hours}小时"
    return f"{minute}分钟"


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def sleep_by_config(config: dict[str, Any]) -> None:
    span = config.get("bbs", {}).get("delay_seconds", [1, 3])
    try:
        low, high = int(span[0]), int(span[1])
    except (TypeError, ValueError, IndexError):
        low, high = 1, 3
    time.sleep(random.uniform(max(low, 0), max(high, low)))
