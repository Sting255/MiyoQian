# -*- coding: utf-8 -*-
"""P6：ipcheck 境外链路必须识别出 country / cc 字段，否则 IP 防护会 fail-open。

ipinfo.io 用 `country`（ISO 码），api.myip.com 用 `cc`，
而 _parse/_enrich 只读 country_code / countryCode。
只要首选接口 api.ipify.org 失败轮到这两条，美国出口会被判成「无法判断」，
再配合默认 on_error=allow 就直接放行——正是这个功能唯一要拦的场景。
"""

from __future__ import annotations

import unittest

from miyouqian.core.ipcheck import IpLocation, LinkReport, classify, _parse

# 真实响应体的形状（已按各接口实际返回裁剪）
IPINFO_BODY = (
    '{"ip":"203.0.113.9","city":"Los Angeles","region":"California",'
    '"country":"US","loc":"34.05,-118.24","org":"AS7922 Comcast"}'
)
MYIP_BODY = '{"ip":"203.0.113.9","country":"United States","cc":"US"}'
IPIFY_BODY = '{"ip":"203.0.113.9"}'
IPIP_TEXT = "当前 IP：203.0.113.9  来自于：中国 江苏  电信"
IPAPI_BODY = '{"status":"success","country":"United States","countryCode":"US",' \
             '"regionName":"California","city":"Los Angeles","query":"203.0.113.9"}'


class OverseaParseTest(unittest.TestCase):
    def test_ipinfo_country_field_is_read(self) -> None:
        location = _parse(IPINFO_BODY)
        self.assertEqual(location.country_code, "US", "ipinfo.io 的 country 字段没被识别")
        self.assertIs(location.mainland, False, "ipinfo 返回的美国 IP 被判成了「无法判断」")

    def test_myip_cc_field_is_read(self) -> None:
        location = _parse(MYIP_BODY)
        self.assertEqual(location.country_code, "US", "api.myip.com 的 cc 字段没被识别")
        self.assertIs(location.mainland, False, "myip 返回的美国 IP 被判成了「无法判断」")

    def test_ipify_only_ip_still_unknown_locally(self) -> None:
        """ipify 只给 IP，归属地要靠 _enrich 反查，这里本身定位不出国家码是正常的。"""
        location = _parse(IPIFY_BODY)
        self.assertEqual(location.ip, "203.0.113.9")
        self.assertEqual(location.country_code, "")

    def test_regression_paths_still_work(self) -> None:
        self.assertIs(classify(_parse(IPAPI_BODY)), False)
        self.assertIs(classify(_parse(IPIP_TEXT)), True)


class BlockedVerdictTest(unittest.TestCase):
    def test_overseas_link_blocks_signin(self) -> None:
        report = LinkReport(
            domestic=IpLocation(ip="49.84.138.105", region="中国 江苏 南京", country_code="CN"),
            overseas=_parse(MYIP_BODY),
        )
        self.assertTrue(report.blocked, "境外链路是 US 却没有拦下来")
        self.assertIs(report.mainland, False)

    def test_ipinfo_overseas_link_blocks_signin(self) -> None:
        report = LinkReport(
            domestic=IpLocation(ip="49.84.138.105", region="中国 江苏 南京", country_code="CN"),
            overseas=_parse(IPINFO_BODY),
        )
        self.assertTrue(report.blocked)
        self.assertIs(report.mainland, False)

    def test_mainland_both_links_is_allowed(self) -> None:
        report = LinkReport(
            domestic=IpLocation(ip="49.84.138.105", region="中国 江苏 南京", country_code="CN"),
            overseas=IpLocation(ip="49.84.138.105", region="中国 江苏 南京", country_code="CN"),
        )
        self.assertFalse(report.blocked)
        self.assertIs(report.mainland, True)


class GuardBehaviourTest(unittest.TestCase):
    def test_guard_blocks_when_overseas_link_is_us(self) -> None:
        """端到端：境外链路查不通时不能因为「无法判断」就放行。"""
        from miyouqian.service.ip_guard import ensure_mainland_ip
        from miyouqian.core.ipcheck import LinkReport as LR

        config = {
            "ip_guard": {"enable": True, "check_interval": 30, "max_wait": 60, "notify": False,
                         "on_error": "allow", "endpoints": []}
        }
        report = LR(
            domestic=IpLocation(ip="49.84.138.105", region="中国 江苏 南京", country_code="CN"),
            overseas=_parse(MYIP_BODY),
        )
        lines: list[str] = []
        allowed = ensure_mainland_ip(
            config,
            lines.append,
            probe_fn=lambda **kwargs: report,
            sleep=lambda seconds: None,  # 不要把 30 秒复查间隔睡出来
        )
        self.assertFalse(allowed, "出口在美国却被 IP 守卫放行了")


if __name__ == "__main__":
    unittest.main()
