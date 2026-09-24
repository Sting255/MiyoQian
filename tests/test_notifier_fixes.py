# -*- coding: utf-8 -*-
"""推送/通知模块的缺陷修复回归测试。

对应交接报告里的发现：
  严重 1  exchange_telegram.html 未转义 → Telegram 整条发送失败 + HTML 注入
  严重 2  账号崩溃后推送标题「失败 0」自相矛盾、崩溃原因看不到
  严重 3  IP 守卫跳过的账号从推送里彻底消失
  严重 4  request_json 只看 HTTP 状态码，业务失败被当成功
  严重 5  cli run 永远退出码 0
  中   6  pushplus/markdown 正文没有长度上限
  中   7  telegram.html 没有 detail 占位符
  中   9  QQ 过滤列表不完整且不截断
  中  10  SMTP 无超时、非 SSL 分支无条件 starttls
  中  11  手动停止后仍被判为「任务成功」
  轻微 14 / 18  死代码
"""

from __future__ import annotations

import pathlib
import re
import unittest
from unittest import mock

import httpx

from miyouqian.service import notifier
from tests.support import IsolatedConfigTest, base_config

CRASH = notifier.ACCOUNT_CRASH_MARKER


def crash_lines() -> list[str]:
    return [
        "# 账号 1/2: 甲",
        f"{CRASH}（甲）：网络请求失败: timeout",
        "# 账号 2/2: 乙",
        "游戏社区签到汇总：成功 3，失败 0，跳过 0",
    ]


def guard_skip_lines() -> list[str]:
    return [
        "# 跳过账号 甲：出口 IP 未恢复",
        "# 账号 1/1: 乙",
        "游戏社区签到汇总：成功 3，失败 0，跳过 0",
    ]


def stopped_lines() -> list[str]:
    return [
        "# 账号 1/2: 甲",
        "游戏社区签到汇总：成功 3，失败 0，跳过 0",
        "# ⏹ 已停止，剩余账号不再执行",
    ]


class FakeResponse:
    def __init__(self, payload, status: int = 200, json_error: bool = False) -> None:
        self._payload = payload
        self.status_code = status
        self._json_error = json_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=mock.Mock(), response=mock.Mock())

    def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls = 0

    def request(self, method: str, url: str, **kwargs):
        self.calls += 1
        return self.response


class ExchangeTelegramEscapeTest(unittest.TestCase):
    """严重 1：商品名/账号名/接口 message 都是外部数据，必须转义。"""

    def test_special_characters_are_escaped(self) -> None:
        out = notifier.build_exchange_telegram(
            "标题",
            "商品<b>名</b>",
            {"ok": True, "retcode": 0, "message": "成功<x>&y", "attempt": 1},
            {"account": "路人<b>甲</b>"},  # web.py 注入的就是 "account" 这个键
            True,
        )
        self.assertIn("商品&lt;b&gt;名&lt;/b&gt;", out)
        self.assertNotIn("商品<b>名</b>", out)
        self.assertIn("路人&lt;b&gt;甲&lt;/b&gt;", out)
        self.assertNotIn("成功<x>&y", out)

    def test_account_name_falls_back_when_caller_does_not_inject_it(self) -> None:
        out = notifier.build_exchange_telegram("标题", "普通商品", {"retcode": 0}, {}, True)
        self.assertIn("未知账号", out)

    def test_template_still_has_its_own_markup(self) -> None:
        out = notifier.build_exchange_telegram("标题", "普通商品", {"retcode": 0, "message": "成功"}, {}, True)
        self.assertIn("<b>", out, "模板自身的加粗标签应当保留")


class AccountCrashSummaryTest(unittest.TestCase):
    """严重 2：崩溃账号要出现在失败里，而不是「失败 0」+ 绿点 + 无任务。"""

    def test_title_is_not_failed_zero(self) -> None:
        title = notifier.build_push_title(crash_lines(), False)
        self.assertNotIn("失败 0", title)
        self.assertIn("失败 1", title)

    def test_crash_reason_is_in_the_failure_list(self) -> None:
        summary = notifier.build_structured_summary(crash_lines(), False)
        failures = summary["overview"]["failed_items"]
        self.assertTrue(any("timeout" in item for item in failures), failures)

    def test_crashed_account_gets_red_dot_and_headline(self) -> None:
        summary = notifier.build_structured_summary(crash_lines(), False)
        html_out = notifier.build_account_sections_html(summary)
        self.assertIn("#b83b4b", html_out, "崩溃账号的状态点应当是红的")
        self.assertIn("执行异常", html_out)
        crashed = [s for s in summary["sections"] if s["label"] == "甲"][0]
        self.assertNotIn("无任务", notifier.account_headline(crashed))

    def test_healthy_run_is_unchanged(self) -> None:
        lines = ["# 账号 1/1: 甲", "游戏社区签到汇总：成功 3，失败 0，跳过 0"]
        summary = notifier.build_structured_summary(lines, True)
        self.assertEqual(summary["overview"]["failed_items"], [])
        self.assertNotIn("#b83b4b", notifier.build_account_sections_html(summary))


class GuardSkipVisibilityTest(unittest.TestCase):
    """严重 3：被 IP 守卫跳过的账号不能从推送里消失。"""

    def test_skipped_account_is_visible(self) -> None:
        lines = guard_skip_lines()
        summary = notifier.build_structured_summary(lines, False)
        body = summary["overview"]["failed_block_text"]
        self.assertIn("出口 IP", body, f"被跳过的账号没出现在正文里: {body!r}")
        title = notifier.build_push_title(lines, False)
        self.assertNotIn("失败 0", title)

    def test_whole_run_skipped_keeps_the_special_title(self) -> None:
        lines = ["# 跳过账号 甲：出口 IP 未恢复", "# 跳过账号 乙：出口 IP 未恢复"]
        self.assertEqual(
            notifier.build_push_title(lines, False),
            "米游签 · 已跳过签到（出口 IP 不在中国大陆）",
        )
        summary = notifier.build_structured_summary(lines, False)
        self.assertTrue(summary["overview"]["failed_block_text"], "正文也应该说明原因")


class ManualStopTest(unittest.TestCase):
    """中 11：手动停止后不能谎报「任务完成」。"""

    def test_stop_is_not_success(self) -> None:
        self.assertFalse(notifier.is_task_success(stopped_lines()))

    def test_stop_is_visible_in_push(self) -> None:
        lines = stopped_lines()
        body = notifier.build_structured_summary(lines, False)["overview"]["failed_block_text"]
        self.assertIn("停止", body)
        self.assertIn("停止", notifier.build_push_title(lines, False))

    def test_normal_completion_still_success(self) -> None:
        lines = ["# 账号 1/1: 甲", "游戏社区签到汇总：成功 3，失败 0，跳过 0"]
        self.assertTrue(notifier.is_task_success(lines))


class BusinessErrorTest(unittest.TestCase):
    """严重 4：HTTP 200 但业务失败必须当成失败。"""

    FAILURES = (
        {"ok": False, "description": "chat not found", "error_code": 400},   # Telegram
        {"code": 201, "msg": "token非法"},                                    # pushplus
        {"errcode": 310000, "errmsg": "sign不匹配"},                          # 钉钉
        {"code": 19021, "msg": "sign match fail"},                            # 飞书
    )
    SUCCESSES = (
        {"ok": True, "result": {"message_id": 1}},   # Telegram
        {"code": 200, "msg": "请求成功"},             # pushplus
        {"errcode": 0, "errmsg": "ok"},              # 钉钉
        {"code": 0, "msg": "success"},               # 飞书
        {},                                          # 空返回体
    )

    def test_business_failures_raise(self) -> None:
        for payload in self.FAILURES:
            with self.subTest(payload=payload):
                client = FakeClient(FakeResponse(payload))
                with self.assertRaises(Exception) as ctx:
                    notifier.request_json(client, "POST", "https://example.invalid/x", json={})
                text = str(ctx.exception)
                self.assertTrue(
                    any(word in text for word in ("chat not found", "token非法", "sign不匹配", "sign match fail")),
                    f"错误信息里没有带上服务端的说明: {text!r}",
                )

    def test_success_shapes_do_not_raise(self) -> None:
        for payload in self.SUCCESSES:
            with self.subTest(payload=payload):
                client = FakeClient(FakeResponse(payload))
                notifier.request_json(client, "POST", "https://example.invalid/x", json={})

    def test_non_json_body_still_ok(self) -> None:
        client = FakeClient(FakeResponse(None, json_error=True))
        notifier.request_json(client, "POST", "https://example.invalid/x", json={})

    def test_http_error_still_raises(self) -> None:
        client = FakeClient(FakeResponse({"ok": True}, status=500))
        with self.assertRaises(httpx.HTTPStatusError):
            notifier.request_json(client, "POST", "https://example.invalid/x", json={})


class CliExitCodeTest(IsolatedConfigTest):
    """严重 5：任务失败时退出码必须是 1。"""

    def _run(self, lines: list[str], success: bool) -> int:
        from miyouqian import cli

        config = base_config()
        self.write_config(config)
        with mock.patch.object(cli, "load_config", lambda path: config), \
             mock.patch.object(cli, "log_path", lambda path, cfg: self.dir / "x.log"), \
             mock.patch.object(cli, "configure_logger", lambda path: None), \
             mock.patch.object(cli, "append_log", lambda *a, **k: None), \
             mock.patch.object(cli, "run_tasks", lambda *a, **k: lines), \
             mock.patch.object(cli, "push_run_result", lambda cfg, task_lines: (success, "")):
            return cli.command_run(self.config_path, None, False, False, None)

    def test_failure_returns_nonzero(self) -> None:
        self.assertEqual(self._run(["游戏社区失败项：原神 失败了"], False), 1)

    def test_success_returns_zero(self) -> None:
        self.assertEqual(self._run(["游戏社区签到汇总：成功 1，失败 0，跳过 0"], True), 0)


class SharedPushHelperTest(unittest.TestCase):
    """中 12：cli 和 web 必须共用同一份「判成功 → 拼标题 → 发推送」。"""

    def test_push_run_result_wires_title_and_success(self) -> None:
        lines = ["# 账号 1/1: 甲", "游戏社区签到汇总：成功 3，失败 0，跳过 0"]
        with mock.patch.object(notifier, "send_push", return_value="ok") as sender:
            success, result = notifier.push_run_result({"push": {}}, lines)
        self.assertTrue(success)
        self.assertEqual(result, "ok")
        kwargs = sender.call_args.kwargs
        self.assertTrue(kwargs["success"])
        self.assertIn("米游签", kwargs["title"] if "title" in kwargs else sender.call_args.args[1])

    def test_failure_path_marks_unsuccessful(self) -> None:
        lines = [
            "# 账号 1/2: 甲",
            f"{CRASH}（甲）：timeout",
            "# 账号 2/2: 乙",
            "游戏社区签到汇总：成功 3，失败 0，跳过 0",
        ]
        with mock.patch.object(notifier, "send_push", return_value=""):
            success, _ = notifier.push_run_result({"push": {}}, lines)
        self.assertFalse(success)

    def test_cli_and_web_no_longer_duplicate_the_logic(self) -> None:
        root = pathlib.Path(notifier.__file__).resolve().parents[1]
        for name in ("cli.py", "service/web.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertIn("push_run_result", text, f"{name} 没有用共用的推送实现")
            self.assertNotIn("build_push_title(lines, success)", text,
                             f"{name} 里还留着重复的推送逻辑")


class ExchangeTimeTest(unittest.TestCase):
    """中 8：兑换推送显示的时间应该是「请求发出的时间」。"""

    def test_uses_sent_at(self) -> None:
        out = notifier.build_exchange_text(
            "标题",
            "商品",
            {"retcode": 0, "message": "兑换成功", "sent_at": "2026-09-23T13:36:47.123"},
            {"account": "甲"},
            True,
        )
        self.assertIn("2026-09-23 13:36:47", out)

    def test_falls_back_when_missing(self) -> None:
        out = notifier.build_exchange_text("标题", "商品", {"retcode": 0, "message": "成功"}, {}, True)
        self.assertIn("时间：", out)


class PushLengthTest(unittest.TestCase):
    """中 6：正文必须有长度上限，别被服务端整条拒收。"""

    def _huge_message(self) -> str:
        rows = [f"游戏社区失败项：账号{i} 失败了很长很长的原因说明{i}" for i in range(400)]
        return "\n".join(rows)

    def test_html_push_is_bounded(self) -> None:
        out = notifier.build_push_html("标题", self._huge_message(), False)
        self.assertLess(len(out), 20000, f"HTML 正文过长: {len(out)} 字符")

    def test_markdown_push_is_bounded(self) -> None:
        out = notifier.build_push_markdown("标题", self._huge_message(), False)
        self.assertLess(len(out), 12000, f"Markdown 正文过长: {len(out)} 字符")

    def test_telegram_push_is_bounded(self) -> None:
        out = notifier.build_telegram_html("标题", self._huge_message(), False)
        self.assertLess(len(out), 6000, f"Telegram 正文过长: {len(out)} 字符")

    def test_short_message_is_not_truncated(self) -> None:
        out = notifier.build_push_html("标题", "游戏社区签到汇总：成功 1，失败 0，跳过 0", True)
        self.assertNotIn("已截断", out)


class TelegramDetailTest(unittest.TestCase):
    """中 7：Telegram 模板要用上 detail，而且必须转义。"""

    def test_detail_placeholder_exists(self) -> None:
        template = (notifier.PUSH_TEMPLATE_DIR / "telegram.html").read_text(encoding="utf-8")
        self.assertIn("{{detail_text}}", template, "telegram.html 没有 detail 占位符")

    def test_detail_is_escaped_for_html_parse_mode(self) -> None:
        out = notifier.build_telegram_html("标题", "# 账号 1/1: 甲\n奇怪<标签>出现", False)
        self.assertNotIn("<标签>", out)


class QqMessageTest(unittest.TestCase):
    """中 9：QQ 通道的过滤要一致，并且有长度上限。"""

    def _message(self) -> str:
        return "\n".join([
            "# 账号 1/1: 甲",
            "正在查询原神 Sting(1)签到状态",
            "游戏社区签到汇总：成功 3，失败 0，跳过 0",
            "游戏社区成功项：原神 已签到",
            "云游戏签到汇总：成功 1，失败 0，跳过 0",
            "云游戏成功项：云原神 已签到",
            "米游币任务汇总：成功 1，失败 0，跳过 0",
            "米游币成功项：大别野社区签到",
            "米游币今日进度：已获得 40",
            "社区任务结束：今日已得 40",
            "游戏社区失败项：" + "x" * 4000,
        ])

    def test_noise_lines_are_filtered_consistently(self) -> None:
        out = notifier.build_qq_message(self._message())
        for noise in ("游戏社区签到汇总", "云游戏签到汇总", "米游币任务汇总",
                      "游戏社区成功项", "云游戏成功项", "米游币成功项",
                      "米游币今日进度", "正在查询"):
            self.assertNotIn(noise, out, f"QQ 消息里还留着噪音行: {noise}")

    def test_result_lines_are_kept(self) -> None:
        out = notifier.build_qq_message(self._message())
        self.assertIn("账号 1/1", out)
        self.assertIn("社区任务结束", out)
        self.assertIn("游戏社区失败项", out)

    def test_message_is_truncated(self) -> None:
        out = notifier.build_qq_message(self._message())
        self.assertLessEqual(len(out), notifier.QQ_MESSAGE_LIMIT + 20)


class MailTimeoutTest(unittest.TestCase):
    """中 10：SMTP 必须有超时，否则配置错主机时任务会无限挂起。"""

    def _send(self, smtp_ssl: bool, has_starttls: bool = True) -> mock.Mock:
        fake = mock.MagicMock()
        fake.__enter__.return_value = fake
        fake.has_extn.return_value = has_starttls
        target = "smtplib.SMTP_SSL" if smtp_ssl else "smtplib.SMTP"
        with mock.patch(target, return_value=fake) as ctor:
            notifier.send_mail(
                smtp_host="smtp.example.com", smtp_port=465 if smtp_ssl else 25,
                smtp_user="u", smtp_password="p", mail_from="a@b.c", mail_to="d@e.f",
                title="t", message="m", html_message="<p>m</p>", smtp_ssl=smtp_ssl,
            )
        self.assertIn("timeout", ctor.call_args.kwargs, f"{target} 没有传 timeout")
        return fake

    def test_ssl_branch_passes_timeout(self) -> None:
        self._send(smtp_ssl=True)

    def test_plain_branch_passes_timeout(self) -> None:
        self._send(smtp_ssl=False)

    def test_starttls_only_when_supported(self) -> None:
        without = self._send(smtp_ssl=False, has_starttls=False)
        without.starttls.assert_not_called()
        with_tls = self._send(smtp_ssl=False, has_starttls=True)
        with_tls.starttls.assert_called()


class TemplateConsistencyTest(unittest.TestCase):
    """模板占位符与代码传的键必须对齐（整类问题一次性防住）。"""

    SAMPLE_OK = "\n".join([
        "# 账号 1/1: 甲",
        "游戏社区签到汇总：成功 3，失败 0，跳过 0",
        "米游币任务汇总：成功 1，失败 0，跳过 0，今日总共可获得 40，实际已获得 40，本次新增 40",
    ])
    SAMPLE_PLAN = {"account": "甲", "price": 100, "icon": ""}
    SAMPLE_RESULT = {"ok": True, "retcode": 0, "message": "兑换成功", "attempt": 1,
                     "sent_at": "2026-09-23T13:36:47.123"}

    def _rendered(self) -> dict[str, str]:
        return {
            "html.html": notifier.build_push_html("标题", self.SAMPLE_OK, True),
            "text.txt": notifier.build_push_text("标题", self.SAMPLE_OK, True),
            "markdown.md": notifier.build_push_markdown("标题", self.SAMPLE_OK, True),
            "telegram.html": notifier.build_telegram_html("标题", self.SAMPLE_OK, True),
            "exchange.html": notifier.build_exchange_html(
                "标题", "商品", self.SAMPLE_RESULT, self.SAMPLE_PLAN, True),
            "exchange_text.txt": notifier.build_exchange_text(
                "标题", "商品", self.SAMPLE_RESULT, self.SAMPLE_PLAN, True),
            "exchange_markdown.md": notifier.build_exchange_markdown(
                "标题", "商品", self.SAMPLE_RESULT, self.SAMPLE_PLAN, True),
            "exchange_telegram.html": notifier.build_exchange_telegram(
                "标题", "商品", self.SAMPLE_RESULT, self.SAMPLE_PLAN, True),
        }

    def test_no_unfilled_placeholders(self) -> None:
        for name, out in self._rendered().items():
            with self.subTest(template=name):
                leftovers = re.findall(r"\{\{(\w+)\}\}", out)
                self.assertEqual(leftovers, [], f"{name} 有没被填充的占位符: {leftovers}")

    def test_every_template_is_covered_by_this_test(self) -> None:
        on_disk = {path.name for path in notifier.PUSH_TEMPLATE_DIR.glob("*") if path.is_file()}
        self.assertEqual(
            on_disk - set(self._rendered()), set(),
            "有模板没被这个测试覆盖，新增模板时要补上",
        )

    def test_context_keys_are_all_used_by_some_template(self) -> None:
        context = notifier.build_template_context("标题", self.SAMPLE_OK, True, detail_limit=100)
        used: set[str] = set()
        for path in notifier.PUSH_TEMPLATE_DIR.glob("*"):
            used |= set(re.findall(r"\{\{(\w+)\}\}", path.read_text(encoding="utf-8")))
        unused = sorted(set(context) - used)
        self.assertEqual(unused, [], f"这些键没有任何模板使用（死代码）: {unused}")


class DeadCodeTest(unittest.TestCase):
    """轻微 14 / 18：死代码删掉，免得误导后来的人。"""

    def test_removed_helpers_are_gone(self) -> None:
        for name in ("extract_summary", "extract_stats", "is_summary_line",
                     "first_number_after", "dedupe", "escape_markdown_code"):
            self.assertFalse(hasattr(notifier, name), f"{name} 应该已经删除")

    def test_removed_helpers_are_unused_in_templates(self) -> None:
        text = (notifier.PUSH_TEMPLATE_DIR / "markdown.md").read_text(encoding="utf-8")
        self.assertNotIn("{{detail_markdown}}", text)


if __name__ == "__main__":
    unittest.main()
