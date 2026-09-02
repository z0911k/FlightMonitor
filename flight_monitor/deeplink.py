"""Google Flights「具体航班订票页」深链构造。

把已解析出的航段（含航司二字码 + 航班号 + 每段出发日）编码进 Google Flights 的
`tfs` protobuf，即可生成两类可复现的链接：

1. `outbound_selected_url` —「预选该趟去程」的搜索链接（纯本地构造、不联网）：
   打开后去程已被锁定为监控的那趟航班，页面直接进入「选返程」。
2. `booking_url_with_return` —「直达该趟往返订票页」的链接（需一次联网抓「选返程」页
   解析出配对返程航段）：打开后就是 Google Flights 的 `booking` 页，含各家 OTA/航司的
   **实付价 + 下单入口**（携程/Booking.com/Expedia/航司官网…），点「Continue」即到该航班订票。

编码格式已用真实抓包做过**字节级校验**（构造结果与实际点击生成的 tfs 完全一致）。
"""
from __future__ import annotations

import base64
from typing import Optional

from .models import Itinerary
from .parser import parse_itineraries

_GF_SEARCH = "https://www.google.com/travel/flights/search"
_GF_BOOKING = "https://www.google.com/travel/flights/booking"
_GF_BASE = "https://www.google.com/travel/flights"


# ---------------- protobuf 编码（varint / string / message） ----------------

def _vi(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            out += bytes([b])
            break
    return out


def _tag(field: int, wt: int) -> bytes:
    return _vi((field << 3) | wt)


def _s(field: int, val: str) -> bytes:
    bb = val.encode("utf-8")
    return _tag(field, 2) + _vi(len(bb)) + bb


def _v(field: int, val: int) -> bytes:
    return _tag(field, 0) + _vi(val)


def _m(field: int, body: bytes) -> bytes:
    return _tag(field, 2) + _vi(len(body)) + body


# tfs 头/尾（1 名成人 + 经济舱 + 往返，常量，已抓包核对）
_HEADER = _v(1, 28) + _v(2, 2)
_TRAILER = _v(8, 1) + _v(9, 1) + _v(14, 1) + _m(16, _v(1, (1 << 64) - 1)) + _v(19, 1)


def _seg_msg(frm: str, date: str, to: str, carrier: str, fnum: str) -> bytes:
    return _s(1, frm) + _s(2, date) + _s(3, to) + _s(5, carrier) + _s(6, fnum)


def _airport_msg(code: str) -> bytes:
    return _v(1, 1) + _s(2, code)


def _leg_msg(leg_date: str, segs: list, origin: str, dest: str,
             max_stops: int = 2, with_segs: bool = True) -> bytes:
    body = _s(2, leg_date)
    if with_segs:
        for (frm, date, to, car, fn) in segs:
            body += _m(4, _seg_msg(frm, date, to, car, fn))
    body += _v(5, max_stops)
    body += _m(13, _airport_msg(origin))
    body += _m(14, _airport_msg(dest))
    return body


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _seg_tuples(itin: Itinerary) -> Optional[list]:
    """把 Itinerary.segments 转成编码所需的 (from, dep_date, to, carrier, flight_no)。

    任一航段缺航司码/航班号/出发日则返回 None（无法可靠构造深链）。
    """
    out = []
    for s in (itin.segments or []):
        date = s.dep_date_str()
        if not (s.frm and s.to and s.carrier and s.flight_no and date):
            return None
        out.append((s.frm, date, s.to, s.carrier, s.flight_no))
    return out or None


def _max_stops(cfg: dict) -> int:
    try:
        return int(cfg.get("max_stops", 2))
    except (TypeError, ValueError):
        return 2


def outbound_selected_tfs(itin: Itinerary, cfg: dict) -> Optional[str]:
    """「预选去程」的 tfs（去程航段完整 + 返程仅日期）。缺航段信息返回 None。"""
    segs = _seg_tuples(itin)
    if not segs or not itin.depart_date or not itin.return_date:
        return None
    ms = _max_stops(cfg)
    leg1 = _leg_msg(itin.depart_date, segs, itin.origin, itin.dest, ms, with_segs=True)
    leg2 = _leg_msg(itin.return_date, [], itin.dest, itin.origin, ms, with_segs=False)
    return _b64(_HEADER + _m(3, leg1) + _m(3, leg2) + _TRAILER)


def _url(base: str, tfs: str, cfg: dict) -> str:
    lang = cfg.get("language", "en-US")
    curr = cfg.get("currency", "CNY")
    return f"{base}?tfs={tfs}&hl={lang}&curr={curr}"


def outbound_selected_url(itin: Itinerary, cfg: dict) -> str:
    """打开后「去程已锁定为这趟航班」的 Google Flights 链接（不联网）。无法构造时返回 ""。"""
    tfs = outbound_selected_tfs(itin, cfg)
    return _url(_GF_SEARCH, tfs, cfg) if tfs else ""


def _booking_tfs(itin: Itinerary, return_itin: Itinerary, cfg: dict) -> Optional[str]:
    out_segs = _seg_tuples(itin)
    ret_segs = _seg_tuples(return_itin)
    if not out_segs or not ret_segs:
        return None
    ms = _max_stops(cfg)
    leg1 = _leg_msg(itin.depart_date, out_segs, itin.origin, itin.dest, ms, with_segs=True)
    leg2 = _leg_msg(itin.return_date, ret_segs, return_itin.origin, return_itin.dest,
                    ms, with_segs=True)
    return _b64(_HEADER + _m(3, leg1) + _m(3, leg2) + _TRAILER)


def _client():
    from primp import Client
    return Client(
        impersonate="chrome_145",
        impersonate_os="macos",
        referer=True,
        cookie_store=True,
    )


def paired_return_options(itin: Itinerary, cfg: dict, *, retries: int = 1) -> Optional[list]:
    """联网抓「选返程」页并解析出所有配对返程。

    返回 list[Itinerary]（每条的 price 即「往返总价」，含 arr_date 可判断落地日），
    或 None（缺航段信息 / 抓取或解析失败 / 无返程）。
    """
    tfs = outbound_selected_tfs(itin, cfg)
    if not tfs:
        return None

    lang = cfg.get("language", "en-US")
    curr = cfg.get("currency", "CNY")
    params = {"tfs": tfs, "hl": lang, "curr": curr}

    html = None
    for _ in range(retries + 1):
        try:
            html = _client().get(_GF_BASE, params=params).text
            break
        except Exception:  # noqa: BLE001
            html = None
    if not html:
        return None

    try:
        rets = parse_itineraries(
            html,
            route_key=f"{itin.dest}-{itin.origin}",
            route_label=itin.route_label,
            depart_date=itin.return_date,
            return_date="",
            url="",
        )
    except Exception:  # noqa: BLE001
        return None
    return rets or None


def booking_url_for(itin: Itinerary, return_itin: Itinerary, cfg: dict) -> str:
    """给定去/返两段，构造直达该趟往返订票页的链接（纯本地构造）。"""
    tfs_book = _booking_tfs(itin, return_itin, cfg)
    return _url(_GF_BOOKING, tfs_book, cfg) if tfs_book else ""


def booking_url_with_return(itin: Itinerary, cfg: dict, *, retries: int = 1):
    """联网抓「选返程」页，配出返程航段，构造直达「该趟往返订票页」的链接。

    返回 (booking_url, return_itin)：
    - 成功：booking_url 为 `/travel/flights/booking?tfs=…`（含各家实付价+下单入口），
      return_itin 为配对的返程行程（可用于展示，价格=往返总价）。
    - 失败（缺航段信息/抓取或解析失败/无可行返程）：返回 ("", None)，调用方应回退。

    若 cfg["arrive_home_by"] 非空，则**只在能按时落地的返程里挑最便宜的**——
    避免「便宜价靠一趟落地已是上班日的超长中转返程」这种假象。
    """
    from .filters import arrives_by  # 延迟导入避免循环依赖

    rets = paired_return_options(itin, cfg, retries=retries)
    if not rets:
        return "", None

    deadline = cfg.get("arrive_home_by", "")
    feasible = [r for r in rets if arrives_by(r, deadline)] if deadline else rets
    if not feasible:
        return "", None

    return_itin = min(feasible, key=lambda x: x.price)
    url = booking_url_for(itin, return_itin, cfg)
    if not url:
        return "", None
    return url, return_itin


def best_booking_url(itin: Itinerary, cfg: dict) -> str:
    """尽力给出「越具体越好」的订票链接（联网失败则退回本地可构造的预选去程链接）。"""
    url, _ = booking_url_with_return(itin, cfg)
    if url:
        return url
    return outbound_selected_url(itin, cfg)
