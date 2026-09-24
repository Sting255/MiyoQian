# -*- coding: utf-8 -*-
"""出口 IP 属地检测。

用于判断本机是不是开着 VPN / 代理从境外出去的：只要公网出口 IP 不在中国大陆，
签到就先停住，等 IP 回到大陆再继续，避免异地登录触发风控。

注意：分流模式（规则模式）的代理下，国内直连、国外走代理，两边看到的出口 IP
是不一样的。只查国内接口会以为一切正常，但米游社是境外服务、走的是代理那条路，
所以必须**分别探测两条链路**，任意一条出境都要拦。
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

# 国内链路探测接口：这些站点的流量通常走直连（代理规则里多是中国大陆直连）
DOMESTIC_ENDPOINTS: tuple[str, ...] = (
    "https://myip.ipip.net/simple",
    "https://ip.3322.net",
    "https://www.taobao.com/help/getip.php",
)

# 境外链路探测接口：这些站点的流量会走代理（如果开了代理的话）
OVERSEAS_ENDPOINTS: tuple[str, ...] = (
    "https://api.ipify.org?format=json",
    "https://ipinfo.io/json",
    "https://api.myip.com",
)

# 只拿到 IP、拿不到归属地时，用它反查。{ip} 会被替换成实际 IP。
GEO_ENDPOINTS: tuple[str, ...] = (
    "http://ip-api.com/json/{ip}?lang=zh-CN",
    "https://ipinfo.io/{ip}/json",
)

# 这些地区虽然属于中国，但网络出口不在大陆，对米游社来说和海外一样是异地。
NON_MAINLAND_HINTS: tuple[str, ...] = ("香港", "澳门", "台湾", "Hong Kong", "Macao", "Macau", "Taiwan")
MAINLAND_HINTS: tuple[str, ...] = ("中国", "大陆", "China", "CN")

IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
REQUEST_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "accept": "*/*",
}


@dataclass
class IpLocation:
    """一次出口 IP 探测的结果。"""

    ip: str = ""
    region: str = ""
    country_code: str = ""
    source: str = ""
    proxy: str = ""
    error: str = ""
    channel: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.ip)

    @property
    def is_private(self) -> bool:
        try:
            return ipaddress.ip_address(self.ip).is_private
        except ValueError:
            return False

    @property
    def mainland(self) -> bool | None:
        """True=中国大陆，False=境外，None=判断不出来。"""
        return classify(self)

    @property
    def label(self) -> str:
        """给人看的一行描述，如「1.2.3.4 · 中国 广东 联通」。"""
        parts = [self.ip or "未知 IP"]
        if self.region:
            parts.append(self.region)
        return " · ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ip": self.ip,
            "region": self.region,
            "country_code": self.country_code,
            "source": self.source,
            "proxy": self.proxy,
            "error": self.error,
            "channel": self.channel,
            "mainland": self.mainland,
        }


@dataclass
class LinkReport:
    """两条链路的综合探测结果。"""

    domestic: IpLocation = field(default_factory=IpLocation)
    overseas: IpLocation = field(default_factory=IpLocation)

    @property
    def split(self) -> bool:
        """国内直连、国外走代理的分流模式：两边 IP 不同。"""
        return bool(
            self.domestic.ip and self.overseas.ip and self.domestic.ip != self.overseas.ip
        )

    @property
    def blocked(self) -> bool:
        """是否需要暂停签到：任意一条链路的出口在境外就拦。"""
        for location in (self.domestic, self.overseas):
            if location.ok and location.mainland is False:
                return True
        return False

    @property
    def mainland(self) -> bool | None:
        """整体判定，语义与 IpLocation.mainland 一致。

        以境外链路为准：米游社是境外服务，走的是和境外链路同一条路。
        境外链路查不通时再退回看国内直连。
        """
        if self.blocked:
            return False
        if self.overseas.ok:
            return self.overseas.mainland
        if self.domestic.ok:
            return self.domestic.mainland
        return None

    @property
    def reason(self) -> str:
        """给日志/推送用的一句话说明。"""
        if self.split:
            return (
                f"国内直连 {self.domestic.label}，"
                f"境外链路 {self.overseas.label}（分流代理，境外流量已走代理）"
            )
        if self.blocked:
            for location in (self.overseas, self.domestic):
                if location.ok and location.mainland is False:
                    return f"当前出口 {location.label}"
        return f"当前出口 {self.domestic.label or self.overseas.label}"

    @property
    def proxy(self) -> str:
        return self.domestic.proxy or self.overseas.proxy

    @property
    def error(self) -> str:
        if not self.domestic.ok and not self.overseas.ok:
            return self.domestic.error or self.overseas.error or "所有查询接口均不可用"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.domestic.to_dict(),
            "domestic": self.domestic.to_dict(),
            "overseas": self.overseas.to_dict(),
            "split": self.split,
            "blocked": self.blocked,
            "mainland": self.mainland,
            "reason": self.reason,
        }


def classify(location: IpLocation) -> bool | None:
    """按归属地文本 + 国家码判断是不是中国大陆出口。"""
    blob = f"{location.region} {location.country_code}"
    for hint in NON_MAINLAND_HINTS:
        if hint in blob:
            return False
    code = (location.country_code or "").strip().upper()
    if code:
        # 港 / 澳 / 台 的 ISO 国家码分别是 HK / MO / TW，都会被当成境外
        return code == "CN"
    for hint in MAINLAND_HINTS:
        if hint in location.region:
            return True
    return None


def _probe_one(client: httpx.Client, url: str) -> IpLocation | None:
    """查单个接口，拿到 IP 就返回，否则返回 None。"""
    try:
        response = client.get(url, headers=REQUEST_HEADERS)
        response.raise_for_status()
        text = response.text.strip()
    except Exception:
        return None
    location = _parse(text)
    if not location.ip:
        return None
    location.source = url
    if not location.country_code and not location.region:
        _enrich(client, location)
    return location


def probe_links(
    timeout: float = 8.0,
    domestic: Iterable[str] | None = None,
    overseas: Iterable[str] | None = None,
    with_proxy: bool = True,
) -> LinkReport:
    """分别探测国内直连链路与境外链路，返回综合判定。"""
    domestic_urls = [str(u).strip() for u in (domestic or ()) if str(u).strip()] or list(DOMESTIC_ENDPOINTS)
    overseas_urls = [str(u).strip() for u in (overseas or ()) if str(u).strip()] or list(OVERSEAS_ENDPOINTS)
    proxy = system_proxy() if with_proxy else ""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        domestic_loc = _probe_first(client, domestic_urls, "domestic")
        overseas_loc = _probe_first(client, overseas_urls, "overseas")
    for location in (domestic_loc, overseas_loc):
        location.proxy = proxy
    return LinkReport(domestic=domestic_loc, overseas=overseas_loc)


def _probe_first(client: httpx.Client, urls: list[str], channel: str) -> IpLocation:
    for url in urls:
        location = _probe_one(client, url)
        if location is not None:
            location.channel = channel
            return location
    return IpLocation(channel=channel, error=f"{channel} 链路的所有接口均不可用")


def _parse(text: str) -> IpLocation:
    text = (text or "").strip()
    payload: Any = None
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
    if isinstance(payload, dict):
        ip = str(payload.get("ip") or payload.get("query") or "")
        if not ip:
            match = IP_PATTERN.search(text)
            ip = match.group(0) if match else ""
        region = _join_region(
            payload.get("country"),
            payload.get("regionName") or payload.get("region"),
            payload.get("city"),
        )
        return IpLocation(
            ip=ip,
            region=region,
            country_code=_country_code(payload),
        )
    match = IP_PATTERN.search(text)
    ip = match.group(0) if match else ""
    region = ""
    if text.startswith("当前 IP"):
        # ipip.net：当前 IP：1.2.3.4  来自于：中国 广东  联通
        parts = text.split("来自于：")
        if len(parts) > 1:
            region = " ".join(parts[1].split())
    return IpLocation(ip=ip, region=region)


def _enrich(client: httpx.Client, location: IpLocation) -> None:
    """只知道 IP 不知道归属地时，再查一次归属地接口。"""
    if not location.ip or location.is_private:
        return
    for template in GEO_ENDPOINTS:
        try:
            response = client.get(template.format(ip=location.ip), headers=REQUEST_HEADERS)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        region = _join_region(
            payload.get("country"),
            payload.get("regionName") or payload.get("region"),
            payload.get("city"),
        )
        if not region and not _country_code(payload):
            continue
        location.region = region
        location.country_code = _country_code(payload)
        return


def _country_code(payload: dict[str, Any]) -> str:
    """从各家接口的返回里取 ISO 国家码。

    字段名并不统一：ip-api 用 countryCode，ipinfo.io 把 ISO 码放在 country（"US"），
    api.myip.com 用 cc。以前只认 country_code / countryCode，
    于是 ipinfo / myip 这两条境外链路即使查到美国 IP 也拿不到国家码，
    classify() 只能返回 None，再配合默认 on_error=allow 就静默放行了——
    这正是 IP 防护唯一要拦的场景。
    """
    for key in ("country_code", "countryCode", "cc"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value.upper()
    country = str(payload.get("country") or "").strip()
    # ipinfo.io 的 country 是两位 ISO 码；myip.com 的 country 是国家全称，交给 region 文本
    if len(country) == 2 and country.isalpha():
        return country.upper()
    return ""


def _join_region(*parts: Any) -> str:
    values: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if text and text not in values:
            values.append(text)
    return " ".join(values)


def system_proxy() -> str:
    """读取系统代理设置，只作为提示信息（VPN 客户端不一定会改它）。"""
    if sys.platform != "win32":
        return ""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""
    server = str(server or "").strip()
    if not enabled or not server:
        return ""
    return server
