# -*- coding: utf-8 -*-
"""任务结果推送。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import pathlib
import re
import smtplib
import time
from datetime import datetime
from email.message import EmailMessage
from typing import Any
from urllib.parse import quote_plus
from ..core.onebot import OneBotHTTP

import httpx
from loguru import logger

PUSH_TEMPLATE_DIR = pathlib.Path(__file__).resolve().parents[1] / "templates" / "push"

# runner 在某个账号整体抛异常（网络/接口错误）时会输出这一行。
# 推送必须据此判成失败，否则「一个账号挂了、另一个正常」会被总结成任务完成。
ACCOUNT_CRASH_MARKER = "❌ 账号执行异常，已跳过该账号剩余任务"

# 正文长度上限：pushplus / 钉钉 / Telegram 对正文都有上限，
# 多账号 + 长异常串时整条推送会被服务端拒收，所以统一截断详细日志。
DETAIL_LIMIT_TELEGRAM = 2600
DETAIL_LIMIT_HTML = 8000
DETAIL_LIMIT_MARKDOWN = 2600

# QQ 通道只发「结果」，进度与汇总行去掉避免刷屏（HTTP API 也有长度限制）。
QQ_SKIP_PREFIXES = (
    "正在",
    "游戏社区签到汇总",
    "游戏签到汇总",
    "云游戏签到汇总",
    "米游币任务汇总",
    "游戏社区成功项",
    "游戏成功项",
    "云游戏成功项",
    "米游币成功项",
    "米游币今日进度",
)
QQ_MESSAGE_LIMIT = 1200

# 各家推送服务返回体里的「成功」码不统一：
# Telegram 看 ok，钉钉看 errcode，pushplus 用 code=200，飞书用 code=0。
PUSH_SUCCESS_CODES = {0, 200, "0", "200", None}

# SMTP 必须有超时，否则主机/端口填错时任务会无限期挂起。
SMTP_TIMEOUT_SECONDS = 20


class PushFailed(RuntimeError):
    """推送服务返回了业务错误（HTTP 200 但内容是失败）。"""


def send_push(config: dict[str, Any], title: str, message: str, success: bool = True) -> str:
    push = config.get("push") or {}
    if not push.get("enable"):
        return ""
    if push.get("error_only") and success:
        return ""
    channels = push_channels(push)
    if not channels:
        return "推送失败: 未配置推送通道"
    results: list[str] = []
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            for channel in channels:
                provider = str(channel.get("provider") or "").strip().lower()
                try:
                    _send(client, provider, channel, title, message, success)
                    results.append(f"{provider}: 成功")
                except Exception as exc:
                    results.append(f"{provider}: 失败 ({exc})")
        return "推送结果: " + "；".join(results)
    except Exception as exc:
        return f"推送失败: {exc}"


def send_exchange_push(
    config: dict[str, Any],
    title: str,
    goods_name: str,
    result: dict[str, Any],
    plan: dict[str, Any],
    success: bool = True,
) -> str:
    """发送商品兑换推送"""
    push = config.get("push") or {}
    if not push.get("enable"):
        return ""
    if push.get("error_only") and success:
        return ""
    channels = push_channels(push)
    if not channels:
        return "推送失败: 未配置推送通道"
    results: list[str] = []
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            for channel in channels:
                provider = str(channel.get("provider") or "").strip().lower()
                try:
                    _send_exchange(client, provider, channel, title, goods_name, result, plan, success)
                    results.append(f"{provider}: 成功")
                except Exception as exc:
                    results.append(f"{provider}: 失败 ({exc})")
        return "推送结果: " + "；".join(results)
    except Exception as exc:
        return f"推送失败: {exc}"


def push_channels(push: dict[str, Any]) -> list[dict[str, Any]]:
    raw_channels = push.get("channels")
    if isinstance(raw_channels, list):
        return [
            channel
            for channel in raw_channels
            if isinstance(channel, dict) and channel.get("enable")
        ]
    if push.get("provider") and push.get("enable"):
        return [push]
    return []


def _send_exchange(
    client: httpx.Client,
    provider: str,
    push: dict[str, Any],
    title: str,
    goods_name: str,
    result: dict[str, Any],
    plan: dict[str, Any],
    success: bool,
) -> None:
    """发送商品兑换推送"""
    token = str(push.get("token") or "").strip()
    webhook = str(push.get("webhook") or "").strip()
    topic = str(push.get("topic") or "").strip()
    chat_id = str(push.get("chat_id") or "").strip()
    secret = str(push.get("secret") or "").strip()
    # QQ推送
    push_url = str(push.get("push_url") or "").strip()
    access_token = str(push.get("access_token") or "").strip()
    send_id = str(push.get("send_id") or "").strip()
    msg_type = str(push.get("msg_type") or "").strip()

    smtp_host = str(push.get("smtp_host") or "").strip()
    smtp_port = int(push.get("smtp_port") or 465)
    smtp_user = str(push.get("smtp_user") or "").strip()
    smtp_password = str(push.get("smtp_password") or "").strip()
    mail_from = str(push.get("mail_from") or smtp_user).strip()
    mail_to = str(push.get("mail_to") or "").strip()
    smtp_ssl = bool(push.get("smtp_ssl", True))

    if provider == "qq":
        require(push_url, "push_url")
        require(access_token, "access_token")
        require(send_id, "send_id")
        require(msg_type, "msg_type")
        if msg_type == "group":
            message_type = 'group'
            user_id = None
            group_id = send_id
        elif msg_type == "private":
            message_type = 'private'
            user_id = send_id
            group_id = None

        text = build_exchange_text(title, goods_name, result, plan, success)
        bot = OneBotHTTP(base_url=push_url, access_token=access_token)
        try:
            bot.send_msg(user_id=user_id, group_id=group_id, message=text, message_type=message_type)
        except Exception as ex:
            raise ValueError(f"QQ推送通道暂不可用，{ex}")
        return

    if provider == "pushplus":
        require(token, "token")
        url = "https://www.pushplus.plus/send"
        payload = pushplus_payload(
            token, title, build_exchange_html(title, goods_name, result, plan, success), "html", topic
        )
        try:
            request_json(client, "POST", url, json=payload)
        except Exception:
            payload = pushplus_payload(
                token, title, build_exchange_markdown(title, goods_name, result, plan, success), "markdown", topic
            )
            request_json(client, "POST", url, json=payload)
        return

    if provider == "telegram":
        require(token, "token")
        require(chat_id, "chat_id")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        request_json(
            client,
            "POST",
            url,
            json={
                "chat_id": chat_id,
                "text": build_exchange_telegram(title, goods_name, result, plan, success),
                "parse_mode": "HTML",
            },
        )
        return

    if provider in {"dingrobot", "dingtalk", "钉钉"}:
        require(webhook, "webhook")
        url = signed_ding_url(webhook, secret) if secret else webhook
        request_json(
            client,
            "POST",
            url,
            json={
                "msgtype": "markdown",
                "markdown": {"title": title, "text": build_exchange_markdown(title, goods_name, result, plan, success)},
            },
        )
        return

    if provider in {"feishubot", "feishu", "飞书"}:
        require(webhook, "webhook")
        lines = [
            f"**{title}**",
            "",
            "**商品兑换通知**",
            f"商品：{goods_name}",
            f"账号：{plan.get('account', '未知')}",
            f"结果：{result.get('message', '未知')}",
            f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        request_json(client, "POST", webhook, json={"msg_type": "text", "content": {"text": "\n".join(lines)}})
        return

    if provider in {"email", "smtp", "mail", "邮箱"}:
        send_mail(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            mail_from=mail_from,
            mail_to=mail_to,
            title=title,
            message=build_exchange_text(title, goods_name, result, plan, success),
            html_message=build_exchange_html(title, goods_name, result, plan, success),
            smtp_ssl=smtp_ssl,
        )
        return

    raise ValueError(f"不支持的推送通道: {provider}")


def _send(client: httpx.Client, provider: str, push: dict[str, Any], title: str, message: str, success: bool) -> None:
    token = str(push.get("token") or "").strip()
    webhook = str(push.get("webhook") or "").strip()
    topic = str(push.get("topic") or "").strip()
    chat_id = str(push.get("chat_id") or "").strip()
    secret = str(push.get("secret") or "").strip()
    # QQ推送
    push_url = str(push.get("push_url") or "").strip()
    access_token = str(push.get("access_token") or "").strip()
    send_id = str(push.get("send_id") or "").strip()
    msg_type = str(push.get("msg_type") or "").strip()

    smtp_host = str(push.get("smtp_host") or "").strip()
    smtp_port = int(push.get("smtp_port") or 465)
    smtp_user = str(push.get("smtp_user") or "").strip()
    smtp_password = str(push.get("smtp_password") or "").strip()
    mail_from = str(push.get("mail_from") or smtp_user).strip()
    mail_to = str(push.get("mail_to") or "").strip()
    smtp_ssl = bool(push.get("smtp_ssl", True))
    markdown_message = build_push_markdown(title, message, success)
    plain_message = build_push_text(title, message, success)

    if provider == "qq":
        require(push_url, "push_url")
        require(access_token, "access_token")
        require(send_id, "send_id")
        require(msg_type, "msg_type")
        # 消息类型
        if msg_type == "group":
            message_type = 'group'
            user_id = None
            group_id = send_id
        elif msg_type == "private":
            message_type = 'private'
            user_id = send_id
            group_id = None
        else:
            # 以前没有这个分支：msg_type 填错会变成 UnboundLocalError，报错完全看不懂
            raise ValueError("QQ 推送的 msg_type 只能是 private 或 group")

        # 统一走 build_qq_message：过滤规则集中一处，并且有长度上限
        qq_message = build_qq_message(message)

        bot = OneBotHTTP(base_url = push_url, access_token = access_token)
        try:
            bot.send_msg(user_id = user_id, group_id = group_id, message = qq_message, message_type = message_type)
        except Exception as ex:
            raise ValueError(f"QQ推送通道暂不可用，{ex}")
        return



    if provider == "pushplus":
        require(token, "token")
        url = "https://www.pushplus.plus/send"
        payload = pushplus_payload(token, title, build_push_html(title, message, success), "html", topic)
        try:
            request_json(client, "POST", url, json=payload)
        except Exception:
            request_json(client, "POST", url, json=pushplus_payload(token, title, markdown_message, "markdown", topic))
        return

    if provider == "telegram":
        require(token, "token")
        require(chat_id, "chat_id")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        request_json(
            client,
            "POST",
            url,
            json={"chat_id": chat_id, "text": build_telegram_html(title, message, success), "parse_mode": "HTML"},
        )
        return

    if provider in {"dingrobot", "dingtalk", "钉钉"}:
        require(webhook, "webhook")
        url = signed_ding_url(webhook, secret) if secret else webhook
        request_json(client, "POST", url, json={"msgtype": "markdown", "markdown": {"title": title, "text": markdown_message}})
        return

    if provider in {"feishubot", "feishu", "飞书"}:
        require(webhook, "webhook")
        request_json(client, "POST", webhook, json=build_feishu_post(title, message, success))
        return

    if provider in {"email", "smtp", "mail", "邮箱"}:
        send_mail(
            smtp_host=smtp_host,
            smtp_port=smtp_port,
            smtp_user=smtp_user,
            smtp_password=smtp_password,
            mail_from=mail_from,
            mail_to=mail_to,
            title=title,
            message=plain_message,
            html_message=build_push_html(title, message, success),
            smtp_ssl=smtp_ssl,
        )
        return

    raise ValueError(f"不支持的推送通道: {provider}")


def build_push_html(title: str, message: str, success: bool) -> str:
    return render_template(
        "html.html",
        build_template_context(title, message, success, detail_limit=DETAIL_LIMIT_HTML),
    )


def pushplus_payload(token: str, title: str, content: str, template: str, topic: str = "") -> dict[str, str]:
    payload = {"token": token, "title": title, "content": content, "template": template}
    if topic:
        payload["topic"] = topic
    return payload


def build_telegram_html(title: str, message: str, success: bool) -> str:
    context = build_template_context(
        title, message, success, detail_limit=DETAIL_LIMIT_TELEGRAM
    )
    # Telegram 用 parse_mode=HTML，这三段都是外部数据拼出来的，必须转义，
    # 否则一个 "<" 就会让整条消息被拒（400 can't parse entities）。
    for key in ("account_sections_text", "overview_text", "detail_text"):
        context[key] = html.escape(context[key])
    return render_template("telegram.html", context)


def build_push_markdown(title: str, message: str, success: bool) -> str:
    return render_template(
        "markdown.md",
        build_template_context(title, message, success, detail_limit=DETAIL_LIMIT_MARKDOWN),
    )


def build_push_text(title: str, message: str, success: bool) -> str:
    return render_template("text.txt", build_template_context(title, message, success))


def build_template_context(title: str, message: str, success: bool, detail_limit: int | None = None) -> dict[str, str]:
    lines = normalize_message_lines(message)
    summary = build_structured_summary(lines, success)
    overview = summary["overview"]
    detail = "\n".join(lines) or "无详细日志"
    if detail_limit is not None:
        detail = truncate_text(detail, detail_limit)
    status = format_status(success)
    return {
        "title": html.escape(title),
        "title_text": title,
        "status": html.escape(status),
        "status_text": status,
        "status_color": "#1c9a68" if success else "#b83b4b",
        "status_bg": "#edf8f3" if success else "#fdecef",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "overview_html": build_overview_html(overview),
        "overview_text": build_overview_text(overview),
        "overview_markdown": build_overview_text(overview, markdown=True),
        "account_sections_html": build_account_sections_html(summary),
        "account_sections_markdown": build_account_sections_text(summary, markdown=True),
        "account_sections_text": build_account_sections_text(summary),
        "detail_html": html.escape(detail),
        "detail_text": detail,
    }


def build_overview_html(overview: dict[str, str]) -> str:
    """总览只在出问题时占版面：一切正常就什么都不渲染（标题已经写了结果）。"""
    return overview.get("failed_block_html") or ""


def build_overview_text(overview: dict[str, str], markdown: bool = False) -> str:
    return overview.get("failed_block_text") or ""


def build_exchange_template_context(
    title: str,
    goods_name: str,
    result: dict[str, Any],
    plan: dict[str, Any],
    success: bool,
) -> dict[str, str]:
    """为商品兑换构建推送模板上下文"""
    status = "兑换成功" if success else "兑换失败"
    retcode = result.get("retcode", -1)
    message = result.get("message", "未知结果")
    attempt = result.get("attempt", 1)

    # 商品信息
    price = plan.get("price", 0)
    icon = plan.get("icon", "")
    account_name = plan.get("account") or plan.get("account_name") or "未知账号"

    # 构建商品图标HTML
    icon_html = ""
    if icon:
        icon_html = f'<img src="{html.escape(icon)}" style="width:60px;height:60px;border-radius:8px;object-fit:cover;margin-right:12px;" alt="商品图标">'
    else:
        icon_html = f'<div style="width:60px;height:60px;border-radius:8px;background:#d8e4ef;display:flex;align-items:center;justify-content:center;margin-right:12px;"><svg width="30" height="30" viewBox="0 0 24 24" fill="#168df5"><path d="M20.38 3.4a1.6 1.6 0 0 0-1.52-.07L13 5.6V4.1a1.6 1.6 0 0 0-2.5-1.3l-8 6.4a1.6 1.6 0 0 0-.2 2.3l8 9.6a1.6 1.6 0 0 0 2.7-.7V13l5.9 3.5a1.6 1.6 0 0 0 2.4-1.4V5a1.6 1.6 0 0 0-1.46-1.6z"/></svg></div>'

    return {
        "title": html.escape(title),
        "title_text": title,
        "status": html.escape(status),
        "status_text": status,
        "status_color": "#1c9a68" if success else "#b83b4b",
        "status_bg": "#edf8f3" if success else "#fdecef",
        # 显示「兑换请求实际发出的时间」而不是「构建推送的时间」：
        # 抢购场景里这两个可能差几秒到几十秒，写构建时间会误导。
        "time": format_sent_at(result.get("sent_at")),
        "goods_name": html.escape(goods_name),
        "goods_name_text": goods_name,
        "price": str(price),
        "account_name": html.escape(account_name),
        "account_name_text": account_name,
        "message": html.escape(message),
        "message_text": message,
        "attempt": str(attempt),
        "icon_html": icon_html,
        "detail_html": html.escape(f"结果：{message}({retcode})，尝试 {attempt} 次"),
    }


def format_sent_at(value: Any) -> str:
    """把 result["sent_at"]（ISO，带毫秒）转成模板里统一的显示格式。"""
    fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = str(value or "").strip()
    if not text:
        return fallback
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return text.replace("T", " ").split(".")[0] or fallback


def section_failures(section: dict[str, Any]) -> list[str]:
    """账号下所有失败项，按「崩溃 → 游戏 → 云游戏 → 米游币」顺序。"""
    items: list[str] = []
    if section.get("crash"):
        items.append(f"执行异常：{section['crash']}")
    items.extend(section["game"]["failed_items"])
    items.extend(section["cloud_game"]["failed_items"])
    items.extend(section["bbs"]["failed_items"])
    return items


def account_headline(section: dict[str, Any]) -> str:
    """一个账号一行就看完的结果摘要（0 项的任务不占位）。"""
    parts: list[str] = []
    if section.get("crash"):
        # 崩溃的账号没有任何汇总行，不写这句就会显示成「无任务」+ 绿点
        parts.append("执行异常")
    game = section["game"]
    if game["present"] and game["success"] + game["failed"] + game["skipped"]:
        parts.append(f"游戏 {game['success']}/{game['success'] + game['failed'] + game['skipped']}")
    cloud = section["cloud_game"]
    if cloud["present"] and cloud["success"] + cloud["failed"] + cloud["skipped"]:
        parts.append(f"云游戏 {cloud['success']}/{cloud['success'] + cloud['failed'] + cloud['skipped']}")
    bbs = section["bbs"]
    if bbs["present"]:
        gained = bbs["gained_points"]
        text = f"米游币 +{gained}"
        if bbs["total_points"]:
            text += f"（{bbs['total_points']}）"
        parts.append(text)
    return " · ".join(parts) or "无任务"


def build_account_sections_html(summary: dict[str, Any]) -> str:
    """一账号一行：左边账号名 + 状态点，右边结果。"""
    sections = summary["sections"]
    if not sections:
        return '<div style="padding:12px 14px;border:1px solid #d8e4ef;border-radius:8px;background:#ffffff;color:#627389;font-size:13px;">本次没有账号实际执行</div>'
    rows: list[str] = []
    for section in sections:
        dot = "#b83b4b" if section_failures(section) else "#1c9a68"
        rows.append(
            f"""
            <div style="padding:11px 13px;border:1px solid #e3ecf4;border-radius:8px;background:#ffffff;margin-bottom:8px;overflow:hidden;">
              <span style="float:right;color:#4a5b70;font-size:13px;line-height:1.5;">{html.escape(account_headline(section))}</span>
              <span style="display:inline-block;width:8px;height:8px;border-radius:999px;background:{dot};margin-right:8px;vertical-align:middle;"></span><span style="color:#102033;font-size:15px;font-weight:700;vertical-align:middle;">{html.escape(section["label"])}</span>
            </div>
            """.strip()
        )
    return "\n".join(rows)


def build_account_sections_text(summary: dict[str, Any], markdown: bool = False) -> str:
    """文本版同样一账号一行。"""
    sections = summary["sections"]
    if not sections:
        return "- 本次没有账号实际执行"
    bullet = "- " if markdown else "· "
    return "\n".join(
        f"{bullet}{section['label']}：{account_headline(section)}" for section in sections
    )


def build_structured_summary(lines: list[str], success: bool) -> dict[str, Any]:
    sections = [
        build_account_summary(section["label"], section["lines"], success)
        for section in split_account_sections(lines)
    ]
    summary = {
        "accounts": [section["label"] for section in sections],
        "sections": sections,
        # 被 IP 守卫跳过 / 手动停止这类「运行级」提示行不属于任何账号，
        # 而且都以 "# " 开头会被 split_account_sections 丢掉，必须单独捞出来。
        "notices": run_level_notices(lines),
    }
    summary["overview"] = build_overview(summary)
    return summary


def _crash_reason(lines: list[str]) -> str:
    """提取账号崩溃那一行里的原因（runner 输出的固定标记）。"""
    for line in lines:
        if ACCOUNT_CRASH_MARKER in line:
            detail = line.split("：", 1)[-1].strip() if "：" in line else ""
            return detail or "账号执行异常"
    return ""


def run_level_notices(lines: list[str]) -> list[str]:
    """不属于任何账号、但必须让用户看到的行。"""
    notices: list[str] = []
    for raw in lines:
        text = raw.strip()
        if text.startswith("# 跳过账号"):
            notices.append(text[2:].strip())
        elif any(marker in text for marker in STOP_MARKERS):
            notices.append(STOP_NOTICE)
    # 去重且保序
    unique: list[str] = []
    for item in notices:
        if item and item not in unique:
            unique.append(item)
    return unique


# 只有「# 账号 1/2: 名字」这种行才算账号分节，
# 其它以 # 开头的提示行（例如「账号间防风控等待」）不应被当成账号卡片
ACCOUNT_HEADER_PATTERN = re.compile(r"^#\s*账号\s*\d+\s*/\s*\d+\s*[:：]")
# runner 中断剩余账号时会输出这些行
STOP_MARKERS = ("剩余账号不再执行",)
# 中断在推送里的统一说法（它不算「失败项」，单列）
STOP_NOTICE = "手动停止，剩余账号未执行"
RESULT_MARKERS = (
    "汇总：",
    "成功项：",
    "失败项：",
    "社区任务结束：",
    "米游币今日进度：",
    "今日任务已完成",
    "配置 enable=false",
    "没有配置账号",
)


def split_account_sections(lines: list[str]) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_label = ""
    current_lines: list[str] = []
    for line in lines:
        if ACCOUNT_HEADER_PATTERN.match(line):
            if current_label or current_lines:
                sections.append(_make_section(current_label, current_lines))
            # 「# 账号 1/2: 大号」在推送里只保留账号名，序号留给用户也没用
            current_label = re.split(r"[:：]", line.lstrip("#").strip(), 1)[-1].strip()
            current_lines = []
            continue
        if line.startswith("# "):
            continue
        current_lines.append(line)
    if current_label or current_lines:
        sections.append(_make_section(current_label, current_lines))
    # 丢掉没有任何结果的杂项段落（日志开头的提示行会形成这种空段落）
    return [section for section in sections if _keep_section(section)]


def _make_section(label: str, lines: list[str]) -> dict[str, Any]:
    return {"label": label or "任务摘要", "lines": lines}


def _keep_section(section: dict[str, Any]) -> bool:
    if section["label"] != "任务摘要":
        return True
    return any(
        marker in line for line in section["lines"] for marker in RESULT_MARKERS
    )


def build_overview(summary: dict[str, Any]) -> dict[str, str]:
    """本次运行的总体结果，放在推送最上方。"""
    sections = summary["sections"]
    notices = [str(item) for item in (summary.get("notices") or [])]
    if not sections:
        return {
            "accounts": "0",
            "headline": "没有账号被执行",
            "detail": "",
            "failed": str(len(notices)),
            "has_failure": "1" if notices else "",
            "failed_items": notices,
            "failed_block_html": build_failed_block_html(notices),
            "failed_block_text": build_failed_block_text(notices),
        }

    totals = {"game_success": 0, "game_total": 0, "cloud_success": 0, "cloud_total": 0}
    gained = 0
    latest_total = 0
    bbs_done = 0
    failures: list[str] = []
    for section in sections:
        for key, prefix in (("game", "game"), ("cloud_game", "cloud")):
            block = section[key]
            if block["present"]:
                totals[f"{prefix}_success"] += block["success"]
                totals[f"{prefix}_total"] += block["success"] + block["failed"] + block["skipped"]
        bbs = section["bbs"]
        if bbs["present"]:
            gained += bbs["gained_points"]
            latest_total = latest_total or bbs["total_points"]
            if bbs["possible_points"]:
                bbs_done += 1
        failures.extend(section_failures(section))
    failures.extend(notices)

    parts: list[str] = []
    if totals["game_total"]:
        parts.append(f"游戏 {totals['game_success']}/{totals['game_total']}")
    if totals["cloud_total"]:
        parts.append(f"云游戏 {totals['cloud_success']}/{totals['cloud_total']}")
    if bbs_done:
        # 不写总余额：多个账号余额各不同，写哪个都会误导，余额放在每个账号行里
        parts.append(f"米游币 +{gained}")

    stopped = any(item == STOP_NOTICE for item in notices)
    real_failures = [item for item in failures if item != STOP_NOTICE]
    return {
        "accounts": str(len(sections)),
        "headline": " · ".join(parts) or "本次未执行任何任务",
        "gained": str(gained),
        "total": str(latest_total) if latest_total else "",
        "failed": str(len(failures)),
        "failed_real": str(len(real_failures)),
        "interrupted": "1" if stopped else "",
        "has_failure": "1" if failures else "",
        "failed_items": failures,
        "failed_block_html": build_failed_block_html(failures),
        "failed_block_text": build_failed_block_text(failures),
    }


def build_failed_block_html(failures: list[str]) -> str:
    if not failures:
        return ""
    rows = "".join(
        f'<div style="margin-top:3px;">· {html.escape(shorten_text(item, 90))}</div>'
        for item in failures[:6]
    )
    more = (
        f'<div style="margin-top:3px;color:#a05a66;">…还有 {len(failures) - 6} 项</div>'
        if len(failures) > 6
        else ""
    )
    return f"""
    <div style="margin-top:14px;padding:10px 12px;border-radius:8px;background:#fdecef;color:#b83b4b;font-size:13px;line-height:1.55;">
      <div style="font-weight:800;margin-bottom:2px;">失败项 {len(failures)} 个</div>
      {rows}{more}
    </div>
    """.strip()


def build_failed_block_text(failures: list[str]) -> str:
    if not failures:
        return ""
    rows = "\n".join(f"· {shorten_text(item, 90)}" for item in failures[:6])
    more = f"\n· …还有 {len(failures) - 6} 项" if len(failures) > 6 else ""
    return f"失败项 {len(failures)} 个：\n{rows}{more}"


def build_push_title(lines: list[str], success: bool) -> str:
    """让推送标题直接带上结果，而不是只写「任务完成」。"""
    summary = build_structured_summary(lines, success)
    overview = summary["overview"]
    if overview["accounts"] == "0":
        if skipped_by_ip_guard(lines):
            return "米游签 · 已跳过签到（出口 IP 不在中国大陆）"
        return "米游签 · " + ("任务完成" if success else "任务失败")
    parts = ["米游签"]
    if overview["accounts"] != "1":
        parts.append(f"{overview['accounts']} 账号")
    headline = overview["headline"]
    if headline and headline != "本次未执行任何任务":
        parts.append(headline)
    # 标题必须说清「为什么不是成功」，且不能出现「失败 0」这种自相矛盾的写法。
    # 手动停止单独一个标记：把它算进「失败 N」会让人以为账号跑挂了。
    marked = False
    real_failed = overview.get("failed_real", overview["failed"])
    if real_failed not in ("", "0"):
        parts.append(f"失败 {real_failed}")
        marked = True
    if overview.get("interrupted"):
        parts.append("已手动停止")
        marked = True
    if not success and not marked:
        parts.append("任务失败")
    return " · ".join(parts)


def build_account_summary(label: str, lines: list[str], success: bool) -> dict[str, Any]:
    game = build_result_summary(
        first_line(lines, "游戏社区签到汇总：") or first_line(lines, "游戏签到汇总："),
        task_items(lines, "游戏社区成功项：") + task_items(lines, "游戏成功项："),
        task_items(lines, "游戏社区失败项：") + task_items(lines, "游戏失败项："),
        success,
    )
    cloud_game = build_result_summary(
        first_line(lines, "云游戏签到汇总："),
        task_items(lines, "云游戏成功项："),
        task_items(lines, "云游戏失败项："),
        success,
    )

    bbs_summary = first_line(lines, "米游币任务汇总：")
    bbs_progress = first_line(lines, "米游币今日进度：")
    bbs_completed = first_line(lines, "今日任务已完成")
    bbs_end = first_line(lines, "社区任务结束：")
    points = parse_point_summary(bbs_summary, bbs_progress, bbs_end, bbs_completed)
    bbs_present = bool(bbs_summary or bbs_progress or any("米游币" in line or "社区签到" in line for line in lines))

    return {
        "label": label,
        "crash": _crash_reason(lines),
        "game": game,
        "cloud_game": cloud_game,
        "bbs": {
            "present": bbs_present,
            "summary": bbs_summary,
            "failed_items": task_items(lines, "米游币失败项："),
            "possible_points": points["possible"],
            "actual_points": points["actual"],
            "gained_points": points["gained"],
            "total_points": points["total"],
            "point_percent": round(points["today_done"] / points["possible"] * 100) if points["possible"] else 0,
            "today_done_points": points["today_done"],
        },
    }


def build_result_summary(summary: str, success_items: list[str], failed_items: list[str], success: bool) -> dict[str, Any]:
    counts = parse_counts(summary)
    total = sum(counts)
    percent = round(counts[0] / total * 100) if total else 0
    label = f"{counts[0]}/{total} 成功" if total else "未执行"
    return {
        "present": bool(summary or success_items or failed_items),
        "summary": summary,
        "success": counts[0],
        "failed": counts[1],
        "skipped": counts[2],
        "percent": percent,
        "label": label,
        "items": success_items,
        "failed_items": failed_items,
    }


def task_items(lines: list[str], prefix: str) -> list[str]:
    items: list[str] = []
    for line in prefixed_lines(lines, prefix):
        items.extend(item.strip() for item in line.removeprefix(prefix).split(";") if item.strip())
    return items


def first_line(lines: list[str], prefix: str) -> str:
    return next((line for line in lines if line.startswith(prefix)), "")


def prefixed_lines(lines: list[str], prefix: str) -> list[str]:
    return [line for line in lines if line.startswith(prefix)]


def parse_counts(line: str) -> tuple[int, int, int]:
    if not line:
        return (0, 0, 0)
    return (
        number_after(line, "成功"),
        number_after(line, "失败"),
        number_after(line, "跳过"),
    )


def is_task_success(lines: list[str]) -> bool:
    # 被 IP 守卫拦下时一个账号都没跑，不算成功，否则推送会显示「任务完成」
    if skipped_by_ip_guard(lines):
        return False
    for line in lines:
        if ACCOUNT_CRASH_MARKER in line:
            return False
        # 手动停止后剩余账号没跑，不能算「任务完成」（error_only 时更会整条不推）
        if any(marker in line for marker in STOP_MARKERS):
            return False
        if line.startswith(("游戏社区失败项：", "游戏失败项：", "云游戏失败项：", "米游币失败项：")):
            return False
        if line.startswith(("游戏社区签到汇总：", "游戏签到汇总：", "云游戏签到汇总：", "米游币任务汇总：")):
            if number_after(line, "失败") > 0:
                return False
        elif line in {"任务状态获取失败，请检查 cookie/stoken", "获取帖子列表失败，无法执行看帖/点赞/分享"}:
            return False
        elif line.startswith(("配置 enable=false", "没有配置账号")):
            return False
    return True


def skipped_by_ip_guard(lines: list[str]) -> bool:
    """本次是不是因为出口 IP 不在中国大陆而整体跳过。"""
    return any("跳过账号" in line and "出口 IP" in line for line in lines)


def parse_point_summary(
    summary_line: str,
    progress_line: str = "",
    end_line: str = "",
    completed_line: str = "",
) -> dict[str, int]:
    initial_received = number_after(progress_line, "已获得")
    actual = number_after(summary_line, "实际已获得")
    if not actual:
        actual = number_after(end_line, "今日已得")
    if not actual:
        actual = initial_received
    possible = number_after(progress_line, "预计总共可获得")
    if not possible:
        possible = number_after(summary_line, "今日总共可获得")
    if not possible:
        possible = initial_received + number_after(progress_line, "还可获得")
    if not possible:
        possible = actual + number_after(end_line, "还能获得")
    today_done = actual or initial_received
    return {
        "actual": actual,
        "possible": possible,
        "today_done": today_done,
        "gained": number_after(summary_line, "本次新增"),
        "total": (
            number_after(end_line, "当前总计")
            or number_after(completed_line, "当前总计")
            or number_after(summary_line, "当前总计")
        ),
    }


def number_after(text: str, keyword: str) -> int:
    if not text:
        return 0
    match = re.search(rf"{re.escape(keyword)}\s*(\d+)", text)
    return int(match.group(1)) if match else 0


def shorten_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def render_template(name: str, values: dict[str, str]) -> str:
    template = (PUSH_TEMPLATE_DIR / name).read_text(encoding="utf-8")
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template.strip()


def build_exchange_html(title: str, goods_name: str, result: dict[str, Any], plan: dict[str, Any], success: bool) -> str:
    """构建商品兑换HTML推送"""
    context = build_exchange_template_context(title, goods_name, result, plan, success)
    return render_template("exchange.html", context)


def build_exchange_telegram(title: str, goods_name: str, result: dict[str, Any], plan: dict[str, Any], success: bool) -> str:
    """构建商品兑换Telegram推送"""
    context = build_exchange_template_context(title, goods_name, result, plan, success)
    # Telegram 走 parse_mode=HTML：商品名 / 账号名 / 接口 message 都是外部数据，
    # 不转义的话商品名里一个 "<" 就会让整条兑换推送被拒收（400 can't parse entities），
    # 顺带也是 HTML 注入面。这四个格式里只有 Telegram 需要转义。
    for key in ("goods_name_text", "account_name_text", "message_text"):
        context[key] = html.escape(context[key])
    return render_template("exchange_telegram.html", context)


def build_exchange_markdown(title: str, goods_name: str, result: dict[str, Any], plan: dict[str, Any], success: bool) -> str:
    """构建商品兑换Markdown推送"""
    context = build_exchange_template_context(title, goods_name, result, plan, success)
    return render_template("exchange_markdown.md", context)


def build_exchange_text(title: str, goods_name: str, result: dict[str, Any], plan: dict[str, Any], success: bool) -> str:
    """构建商品兑换文本推送"""
    context = build_exchange_template_context(title, goods_name, result, plan, success)
    return render_template("exchange_text.txt", context)


def build_feishu_post(title: str, message: str, success: bool) -> dict[str, Any]:
    lines = normalize_message_lines(message)
    summary = build_structured_summary(lines, success)
    content: list[list[dict[str, str]]] = [
        [{"tag": "text", "text": f"状态：{format_status(success)} · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"}],
        [{"tag": "text", "text": ""}],
    ]
    for section in summary["sections"]:
        content.append([{"tag": "text", "text": f"{section['label']}：{account_headline(section)}"}])
    failed_text = summary["overview"]["failed_block_text"]
    if failed_text:
        content.append([{"tag": "text", "text": ""}])
        content.append([{"tag": "text", "text": failed_text}])
    return {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": content,
                }
            }
        },
    }


def normalize_message_lines(message: str) -> list[str]:
    normalized = message.replace("\r\n", "\n").replace("\r", "\n")
    return [line.rstrip() for line in normalized.split("\n") if line.strip()]


def format_status(success: bool) -> str:
    return "成功" if success else "失败"


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(limit - 20, 0)] + "\n... 已截断"


def request_json(client: httpx.Client, method: str, url: str, **kwargs: Any) -> Any:
    """发请求并校验业务结果。

    只看 HTTP 状态码是不够的：pushplus / Telegram / 钉钉 / 飞书在 token、
    chat_id、签名出错时都返回 **HTTP 200 + 错误 JSON**，所以这里还要看业务码，
    否则用户会一直看到「推送成功」，却永远收不到消息也查不出原因。
    """
    response = client.request(method, url, **kwargs)
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError:
        return None
    error = push_business_error(data)
    if error:
        raise PushFailed(error)
    return data


def push_business_error(data: Any) -> str:
    """从各家返回体里认出「HTTP 200 但业务失败」，返回出错说明（空串表示成功）。"""
    if not isinstance(data, dict):
        return ""
    if data.get("ok") is False:  # Telegram
        detail = data.get("description") or data.get("msg") or data.get("error_code") or ""
        return f"接口返回失败: {detail}".strip()
    for key in ("errcode", "code"):  # 钉钉 / pushplus / 飞书
        if key not in data:
            continue
        value = data[key]
        if value in PUSH_SUCCESS_CODES:
            continue
        detail = data.get("errmsg") or data.get("msg") or data.get("message") or ""
        return f"{key}={value} {detail}".strip()
    return ""


def build_qq_message(message: str, limit: int = QQ_MESSAGE_LIMIT) -> str:
    """把任务日志压成适合 QQ 发的短消息（去掉进度噪音并按长度截断）。"""
    kept: list[str] = []
    for raw in message.splitlines():
        text = raw.strip()
        if not text or text.endswith("社区签到成功"):
            continue
        if any(text.startswith(prefix) for prefix in QQ_SKIP_PREFIXES):
            continue
        kept.append(text)
    return truncate_text("\n".join(kept), limit)


def push_run_result(config: dict[str, Any], lines: list[str]) -> tuple[bool, str]:
    """一轮任务跑完后统一推送，返回 (是否成功, 推送结果说明)。

    cli 和 web 以前各写一份同样的「判成功 → 拼标题 → 发推送」，
    改一处漏一处；现在只有这一份实现。
    """
    success = is_task_success(lines)
    result = send_push(
        config,
        build_push_title(lines, success),
        "\n".join(lines),
        success=success,
    )
    return success, result


def require(value: str, name: str) -> None:
    if not value:
        raise ValueError(f"缺少 {name}")


def signed_ding_url(webhook: str, secret: str) -> str:
    timestamp = str(round(time.time() * 1000))
    sign_data = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), sign_data, hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in webhook else "?"
    return f"{webhook}{separator}timestamp={timestamp}&sign={sign}"


def send_mail(
    *,
    smtp_host: str,
    smtp_port: int,
    smtp_user: str,
    smtp_password: str,
    mail_from: str,
    mail_to: str,
    title: str,
    message: str,
    html_message: str,
    smtp_ssl: bool,
) -> None:
    require(smtp_host, "smtp_host")
    require(smtp_user, "smtp_user")
    require(smtp_password, "smtp_password")
    require(mail_from, "mail_from")
    require(mail_to, "mail_to")
    email = EmailMessage()
    email["Subject"] = title
    email["From"] = mail_from
    email["To"] = mail_to
    email.set_content(message)
    email.add_alternative(html_message, subtype="html")
    recipients = [item.strip() for item in mail_to.split(",") if item.strip()]
    if smtp_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(email, to_addrs=recipients)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            # 只有服务器声明支持才 STARTTLS：无条件调用会让「不支持 TLS 的 SMTP」
            # 直接抛异常，而这个分支本来就是给纯明文服务器用的。
            if smtp.has_extn("starttls"):
                smtp.starttls()
                smtp.ehlo()
            else:
                logger.bind(component="push").warning(
                    "SMTP 服务器不支持 STARTTLS，将明文发送；建议改用 smtp_ssl: true"
                )
            smtp.login(smtp_user, smtp_password)
            smtp.send_message(email, to_addrs=recipients)
