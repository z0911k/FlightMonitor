"""Google Flights `ds:1` 内嵌 JSON 解析器。

fast-flights 3.0.2 自带解析器因 Google 改版而崩溃（顶部联盟/航司元数据索引失效）。
这里只使用 `k0` 中语义稳定的字段，直接从 payload 中提取行程与价格，更抗改版。
"""
from __future__ import annotations

import json
from typing import Optional

from selectolax.lexbor import LexborHTMLParser

from .models import Itinerary, Layover, Segment


class ParseError(Exception):
    pass


def _time2(v):
    """把 Google 的时间数组规范成 (h, m)：整点会被编码成单元素 [h]，None 视作 0。"""
    if not v:
        return None
    h = v[0] if len(v) > 0 and v[0] is not None else 0
    m = v[1] if len(v) > 1 and v[1] is not None else 0
    try:
        return (int(h), int(m))
    except (TypeError, ValueError):
        return None


def _extract_payload(html: str):
    parser = LexborHTMLParser(html)
    scripts = parser.css(r"script.ds\:1")
    if not scripts:
        raise ParseError("未找到 ds:1 脚本（页面结构异常或被拦截）")
    txt = scripts[0].text()
    try:
        data = txt.split("data:", 1)[1].rsplit(",", 1)[0]
    except IndexError as e:  # noqa: F841
        raise ParseError("ds:1 脚本中未找到 data 段")
    if data.rstrip().endswith("errorHasStatus: true"):
        raise ParseError("Google 返回错误状态")
    return json.loads(data)


def parse_itineraries(
    html: str,
    *,
    route_key: str,
    route_label: str,
    depart_date: str,
    return_date: str,
    url: str = "",
) -> list[Itinerary]:
    """解析一页往返结果。无结果时返回空列表（不抛异常）。"""
    payload = _extract_payload(html)

    # Google Flights 把结果分成两组：
    #   payload[2][0] = “最佳航班”（通常含真正最便宜的优质组合）
    #   payload[3][0] = “其它航班”（补充结果，价格往往偏高）
    # 旧版只读 payload[3]，会整组漏掉更便宜的“最佳航班”，导致显示价系统性偏高、
    # 看起来还老不动。这里两组都读并去重，确保捞到全网最低。
    groups: list = []
    for idx in (2, 3):
        if len(payload) > idx and isinstance(payload[idx], list) and payload[idx]:
            g = payload[idx][0]
            if isinstance(g, list):
                groups.extend(g)
    if not groups:
        return []

    itins: list[Itinerary] = []
    seen: set = set()
    for k in groups:
        try:
            itin = _parse_group(
                k,
                route_key=route_key,
                route_label=route_label,
                depart_date=depart_date,
                return_date=return_date,
                url=url,
            )
        except (IndexError, TypeError, KeyError):
            continue  # 单个行程结构异常则跳过，不影响其它
        if itin is None:
            continue
        key = (itin.price, itin.stops, itin.total_min,
               itin.dep_time, itin.arr_time, tuple(itin.airlines))
        if key in seen:
            continue  # 两组间可能重复，去重
        seen.add(key)
        itins.append(itin)
    return itins


def _parse_group(k, *, route_key, route_label, depart_date, return_date, url) -> Optional[Itinerary]:
    d = k[0]
    price = None
    try:
        price = k[1][0][1]
    except (IndexError, TypeError):
        pass
    if not isinstance(price, (int, float)):
        return None

    origin = d[3]
    dest = d[6]
    dep_date = tuple(d[4]) if d[4] else None
    dep_time = _time2(d[5])
    arr_date = tuple(d[7]) if len(d) > 7 and d[7] else None
    arr_time = _time2(d[8])
    total_min = d[9]
    stops = d[10] if isinstance(d[10], int) else max(0, len(d[2] or []) - 1)

    segments = []
    for s in (d[2] or []):
        carrier = flight_no = ""
        if len(s) > 22 and isinstance(s[22], (list, tuple)):
            if len(s[22]) > 0 and isinstance(s[22][0], str):
                carrier = s[22][0]
            if len(s[22]) > 1 and s[22][1] is not None:
                flight_no = str(s[22][1])
        segments.append(
            Segment(
                frm=s[3],
                to=s[6],
                dep_time=_time2(s[8]),
                arr_time=_time2(s[10]),
                dur_min=s[11] if isinstance(s[11], int) else None,
                frm_name=s[4] if len(s) > 4 and isinstance(s[4], str) else "",
                to_name=s[5] if len(s) > 5 and isinstance(s[5], str) else "",
                dep_date=tuple(s[20]) if len(s) > 20 and s[20] else None,
                carrier=carrier,
                flight_no=flight_no,
            )
        )

    layovers = []
    for lv in (d[13] or []):
        try:
            layovers.append(
                Layover(
                    code=lv[1],
                    minutes=int(lv[0]),
                    name=lv[4] if len(lv) > 4 and isinstance(lv[4], str) else "",
                    city=lv[5] if len(lv) > 5 and isinstance(lv[5], str) else "",
                )
            )
        except (IndexError, TypeError, ValueError):
            continue

    airlines = list(d[1]) if isinstance(d[1], list) else []
    origin_name = segments[0].frm_name if segments else ""
    dest_name = segments[-1].to_name if segments else ""

    return Itinerary(
        route_key=route_key,
        route_label=route_label,
        origin=origin,
        dest=dest,
        price=int(price),
        stops=int(stops),
        total_min=int(total_min) if isinstance(total_min, int) else 0,
        dep_date=dep_date,
        dep_time=dep_time,
        arr_date=arr_date,
        arr_time=arr_time,
        origin_name=origin_name,
        dest_name=dest_name,
        airlines=airlines,
        segments=segments,
        layovers=layovers,
        depart_date=depart_date,
        return_date=return_date,
        url=url,
    )
