"""各 OTA「一键比价」深链接（携程 / Trip.com / 去哪儿 / Skyscanner / 飞猪 / 同程）。

说明：这些平台没有免费公开的机票 API，且反爬严格。这里不抓取它们的数据，
只按当前最优航线+日期拼出「搜索结果页」链接，用浏览器打开即可秒对价，稳定且不违规。
- 携程 / Trip.com / 去哪儿 / Skyscanner：直达往返结果页（已预填航线与去/返日期）。
- 飞猪 / 同程：无稳定的预填往返深链，退化为「打开机票页」（落到正确航线/日期，需再点一次搜索）。
"""
from __future__ import annotations


def _yymmdd(d: str) -> str:   # 2026-09-26 -> 260926
    return d.replace("-", "")[2:]


def _ctrip_round(o: str, d: str, dep: str, ret: str) -> str:
    """携程当前有效的往返结果页（国内+国际通用），可直接下单。"""
    return (
        f"https://flights.ctrip.com/online/list/round-{o}-{d}"
        f".html?depdate={dep}&rtnDate={ret}&cabin=y&adult=1&child=0&infant=0"
    )


def book_links(origin: str, dest: str, dep: str, ret: str, gf_url: str = "",
               book_url: str = "") -> list[dict]:
    """「去订票」入口：精确航线+去/返日期。

    关键事实：小窗显示的最低价来自 Google Flights 的聚合，携程/Trip.com 是各自独立的
    定价体系（价格未必相同）。能做到的最优是把用户带到**这趟具体航班**：
    - `book_url`（若有）放**第一位**：直达 Google Flights 的「该趟往返订票页」，
      页面直接列出各家 OTA/航司的实付价+下单入口（点 Continue 即到该航班订票）；
    - 否则退回 `gf_url`（本趟价格来源的搜索页，需在结果里点开那趟）；
    - 携程 / Trip.com 随后：国内可直接下单，但价格以其页面为准。
    返回 [{name, note, url}]。
    """
    o, d = origin.upper(), dest.upper()
    ol, dl = o.lower(), d.lower()
    items = []
    if book_url:
        items.append(
            {"name": "Google Flights", "note": "★直达这趟航班订票页 · 各家实付价可下单", "url": book_url}
        )
    elif gf_url:
        items.append(
            {"name": "Google Flights", "note": "★本趟价格来源 · 点开那趟即可下单", "url": gf_url}
        )
    items += [
        {
            "name": "携程",
            "note": "国内直接下单 · 价格以携程为准",
            "url": _ctrip_round(o, d, dep, ret),
        },
        {
            "name": "Trip.com",
            "note": "携程国际 · 价格以其页面为准",
            "url": (
                f"https://www.trip.com/flights/{ol}-{dl}/tickets?tripType=rt"
                f"&date={dep}&rdate={ret}&cabinClass=economy&adult=1&curr=CNY&locale=zh-CN"
            ),
        },
    ]
    return items


def primary_book_url(origin: str, dest: str, dep: str, ret: str, gf_url: str = "",
                     book_url: str = "") -> str:
    """单入口订票链接——用于 toast「去订票」按钮。

    优先返回直达本趟航班订票页的 `book_url`，其次 Google Flights 搜索页，再退回携程往返页。
    """
    return book_links(origin, dest, dep, ret, gf_url, book_url)[0]["url"]


def compare_links(origin: str, dest: str, dep: str, ret: str, gf_url: str = "") -> list[dict]:
    """返回比价入口列表：[{name, note, url, direct}]。direct=是否直达往返结果页。"""
    o, d = origin.upper(), dest.upper()
    ol, dl = o.lower(), d.lower()

    items = [
        {
            "name": "携程",
            "note": "直达往返结果",
            "direct": True,
            "url": _ctrip_round(o, d, dep, ret),
        },
        {
            "name": "Trip.com",
            "note": "携程国际 · 直达往返",
            "direct": True,
            "url": (
                f"https://www.trip.com/flights/{ol}-{dl}/tickets?tripType=rt"
                f"&date={dep}&rdate={ret}&cabinClass=economy&adult=1&curr=CNY&locale=zh-CN"
            ),
        },
        {
            "name": "Skyscanner",
            "note": "元搜索 · 直达往返",
            "direct": True,
            "url": (
                f"https://www.skyscanner.net/transport/flights/{ol}/{dl}/"
                f"{_yymmdd(dep)}/{_yymmdd(ret)}/?adults=1&cabinclass=economy"
            ),
        },
    ]
    if gf_url:
        items.append(
            {"name": "Google Flights", "note": "元搜索 · 直达往返", "direct": True, "url": gf_url}
        )
    items += [
        {
            "name": "飞猪",
            "note": "打开机票页 · 自行搜索",
            "direct": False,
            "url": "https://www.fliggy.com/jipiao/",
        },
        {
            "name": "同程",
            "note": "打开机票页 · 已填去程",
            "direct": False,
            "url": f"https://www.ly.com/flights/itinerary/oneway/{o}-{d}?date={dep}",
        },
    ]
    return items
