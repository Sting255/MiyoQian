# -*- coding: utf-8 -*-
"""IP 查询单链路失效、结果回退与界面判定的回归测试；不访问真实网络。"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import unittest
from unittest import mock

import httpx

from miyouqian.core.ipcheck import IpLocation, LinkReport, probe_links
from miyouqian.service.ip_guard import ensure_mainland_ip


def location(ip: str, country: str, channel: str = "") -> IpLocation:
    return IpLocation(ip=ip, country_code=country, region=country, channel=channel)


class FallbackReportTest(unittest.TestCase):
    def test_successful_backup_supplies_top_level_location_and_reason(self) -> None:
        report = LinkReport(
            domestic=IpLocation(error="国内查询不可用", channel="domestic"),
            overseas=location("203.0.113.9", "CN", "overseas"),
        )
        result = report.to_dict()
        self.assertIs(report.mainland, True)
        self.assertFalse(report.blocked)
        self.assertEqual(result["ip"], "203.0.113.9")
        self.assertEqual(result["country_code"], "CN")
        self.assertEqual(result["channel"], "overseas")
        self.assertEqual(result["error"], "")
        self.assertIn("203.0.113.9", report.reason)
        self.assertNotIn("未知 IP", report.reason)
        self.assertEqual(result["domestic"]["error"], "国内查询不可用")

    def test_domestic_remains_fallback_when_overseas_query_fails(self) -> None:
        report = LinkReport(domestic=location("203.0.113.8", "CN"))
        self.assertIs(report.mainland, True)
        self.assertEqual(report.location.ip, "203.0.113.8")
        self.assertEqual(report.to_dict()["ip"], "203.0.113.8")

    def test_displayed_blocked_location_is_the_offending_link(self) -> None:
        for domestic_country, overseas_country, expected_ip in (
            ("CN", "US", "203.0.113.9"),
            ("US", "CN", "203.0.113.8"),
        ):
            with self.subTest(domestic=domestic_country, overseas=overseas_country):
                report = LinkReport(
                    domestic=location("203.0.113.8", domestic_country),
                    overseas=location("203.0.113.9", overseas_country),
                )
                self.assertTrue(report.blocked)
                self.assertIs(report.mainland, False)
                self.assertEqual(report.to_dict()["ip"], expected_ip)

    def test_different_mainland_addresses_do_not_imply_proxy_or_block(self) -> None:
        report = LinkReport(
            domestic=location("203.0.113.8", "CN"),
            overseas=location("203.0.113.9", "CN"),
        )
        self.assertTrue(report.split)
        self.assertFalse(report.blocked)
        self.assertIs(report.mainland, True)
        self.assertNotIn("已走代理", report.reason)
        self.assertIn("出口不同", report.reason)

    def test_all_failed_queries_remain_unknown(self) -> None:
        report = LinkReport(
            domestic=IpLocation(error="国内不可用"),
            overseas=IpLocation(error="备用不可用"),
        )
        result = report.to_dict()
        self.assertIsNone(result["mainland"])
        self.assertFalse(result["blocked"])
        self.assertEqual(result["ip"], "")
        self.assertEqual(result["error"], "国内不可用")

    def test_unknown_overseas_location_does_not_borrow_domestic_country(self) -> None:
        report = LinkReport(
            domestic=location("203.0.113.8", "CN"),
            overseas=IpLocation(ip="203.0.113.9"),
        )
        self.assertIsNone(report.mainland)
        self.assertEqual(report.to_dict()["ip"], "203.0.113.9")
        self.assertEqual(report.to_dict()["country_code"], "")


class FallbackProbeAndGuardTest(unittest.TestCase):
    def test_empty_tls_and_http_failures_still_reach_successful_backup(self) -> None:
        visited: list[str] = []

        def respond(request: httpx.Request) -> httpx.Response:
            visited.append(str(request.url))
            if request.url.host == "empty.test":
                return httpx.Response(204)
            if request.url.host == "tls.test":
                raise httpx.ConnectError("certificate has expired", request=request)
            if request.url.host == "forbidden.test":
                return httpx.Response(403)
            return httpx.Response(200, json={"ip": "203.0.113.9", "country": "CN"})

        client = httpx.Client(transport=httpx.MockTransport(respond))
        with mock.patch("miyouqian.core.ipcheck.httpx.Client", return_value=client):
            report = probe_links(
                domestic=["https://empty.test", "https://tls.test", "https://forbidden.test"],
                overseas=["https://backup.test"],
                with_proxy=False,
            )
        self.assertEqual(len(visited), 4)
        self.assertFalse(report.domestic.ok)
        self.assertTrue(report.overseas.ok)
        self.assertEqual(report.to_dict()["ip"], "203.0.113.9")
        self.assertIs(report.mainland, True)

    def test_guard_success_and_recovery_messages_use_backup_location(self) -> None:
        fallback = LinkReport(overseas=location("203.0.113.9", "CN"))
        blocked = LinkReport(overseas=location("203.0.113.9", "US"))
        config = {"ip_guard": {"enable": True, "notify": True, "max_wait": 60,
                               "check_interval": 30, "on_error": "allow"}}
        for reports in ([fallback], [blocked, fallback]):
            with self.subTest(recovery=len(reports) > 1):
                lines: list[str] = []
                with mock.patch("miyouqian.service.ip_guard.send_push") as push:
                    allowed = ensure_mainland_ip(
                        config, lines.append, probe_fn=mock.Mock(side_effect=reports),
                        sleep=lambda seconds: None,
                    )
                self.assertTrue(allowed)
                self.assertIn("203.0.113.9", lines[-1])
                self.assertNotIn("未知 IP", " ".join(lines))
                if len(reports) > 1:
                    self.assertIn("203.0.113.9", push.call_args.args[2])

    def test_unknown_block_policy_does_not_claim_known_overseas_exit(self) -> None:
        config = {"ip_guard": {"enable": True, "notify": False, "max_wait": 30,
                               "check_interval": 30, "on_error": "block"}}
        lines: list[str] = []
        self.assertFalse(ensure_mainland_ip(
            config, lines.append, probe_fn=lambda **kwargs: LinkReport(),
            sleep=lambda seconds: None,
        ))
        self.assertIn("无法确认出口 IP 属地", " ".join(lines))
        self.assertNotIn("检测到境外出口", " ".join(lines))


@unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend behavior checks")
class FrontendIpResultTest(unittest.TestCase):
    def render(self, report: LinkReport) -> str:
        path = pathlib.Path(__file__).resolve().parents[1] / "miyouqian" / "webui" / "app.js"
        source = path.read_text(encoding="utf-8")
        match = re.search(r"async function checkIpNow\(\) \{.*?\n\}", source, re.S)
        self.assertIsNotNone(match)
        harness = r"""
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const node = {textContent: ''};
let toast = '';
const run = new Function('$', 'api', 'showToast', input.source + '\nreturn checkIpNow();');
run(() => node, async () => input.data, (text) => {toast = text;})
  .then(() => process.stdout.write(JSON.stringify({text: node.textContent, toast})))
  .catch((error) => {console.error(error); process.exitCode = 1;});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", harness],
            input=json.dumps({"source": match.group(0), "data": report.to_dict()}),
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["text"], rendered["toast"])
        return rendered["text"]

    def test_backup_success_is_visible(self) -> None:
        text = self.render(LinkReport(overseas=location("203.0.113.9", "CN")))
        self.assertIn("中国大陆：203.0.113.9", text)
        self.assertIn("备用查询结果", text)
        self.assertNotIn("未知 IP", text)

    def test_split_with_both_links_mainland_is_not_shown_as_paused(self) -> None:
        text = self.render(LinkReport(
            domestic=location("203.0.113.8", "CN"),
            overseas=location("203.0.113.9", "CN"),
        ))
        self.assertIn("中国大陆", text)
        self.assertNotIn("已暂停", text)
        self.assertNotIn("会触发异地风控", text)
        self.assertNotIn("分流代理", text)

    def test_overseas_warning_displays_offending_link(self) -> None:
        text = self.render(LinkReport(
            domestic=location("203.0.113.8", "CN"),
            overseas=location("203.0.113.9", "US"),
        ))
        self.assertIn("检测到境外出口：203.0.113.9", text)
        self.assertNotIn("已暂停", text)
        self.assertIn("IP 防护开启时", text)


if __name__ == "__main__":
    unittest.main()
