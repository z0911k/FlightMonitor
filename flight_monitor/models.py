"""数据模型。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from . import airports

_WEEKDAY = "一二三四五六日"


def _hm(t) -> str:
    """(h, m) -> 'HH:MM'；容忍整点单元素 [h] 与 None。"""
    if not t:
        return "--:--"
    try:
        h = int(t[0]) if len(t) > 0 and t[0] is not None else 0
        m = int(t[1]) if len(t) > 1 and t[1] is not None else 0
    except (TypeError, ValueError):
        return "--:--"
    return f"{h:02d}:{m:02d}"


def _dur(minutes: Optional[int]) -> str:
    """分钟 -> 'Xh YYm'。"""
    if minutes is None:
        return "-"
    minutes = int(minutes)
    return f"{minutes // 60}h{minutes % 60:02d}"


def _to_date(t) -> Optional[datetime.date]:
    if not t or len(t) < 3:
        return None
    try:
        return datetime.date(int(t[0]), int(t[1]), int(t[2]))
    except (ValueError, TypeError):
        return None


def weekday_cn(t) -> str:
    d = _to_date(t)
    return _WEEKDAY[d.weekday()] if d else ""


def date_label(t) -> str:
    """(y,m,d) -> 'MM-DD 周X'。"""
    d = _to_date(t)
    if not d:
        return ""
    return f"{d.month:02d}-{d.day:02d} 周{_WEEKDAY[d.weekday()]}"


@dataclass
class Segment:
    frm: str
    to: str
    dep_time: tuple  # (h, m)
    arr_time: tuple
    dur_min: int
    frm_name: str = ""
    to_name: str = ""
    dep_date: tuple = None   # (y, m, d) 本航段出发日
    carrier: str = ""        # 航司二字码，如 "CX"
    flight_no: str = ""      # 航班号，如 "337"

    def dep_date_str(self) -> str:
        if not self.dep_date or len(self.dep_date) < 3:
            return ""
        return f"{int(self.dep_date[0]):04d}-{int(self.dep_date[1]):02d}-{int(self.dep_date[2]):02d}"


@dataclass
class Layover:
    code: str
    minutes: int
    name: str = ""
    city: str = ""

    def label(self) -> str:
        return f"{self.code} {_dur(self.minutes)}"

    def city_cn(self) -> str:
        return airports.city_name(self.code, self.city or self.name)

    def label_cn(self) -> str:
        return f"{self.city_cn()} 停{_dur(self.minutes)}"


@dataclass
class Itinerary:
    route_key: str          # 例如 "PEK-SYD"
    route_label: str
    origin: str
    dest: str
    price: int              # 往返总价（货币见 config.currency）
    stops: int
    total_min: int
    dep_date: tuple         # (y, m, d) 去程出发日
    dep_time: tuple         # (h, m)
    arr_time: tuple
    airlines: list          # 航司名称
    segments: list          # list[Segment]（去程航段）
    layovers: list          # list[Layover]
    depart_date: str        # "2026-09-26"
    return_date: str        # "2026-10-05"
    arr_date: tuple = None   # (y, m, d) 到达日（可能跨日）
    origin_name: str = ""    # 出发机场英文名（回退用）
    dest_name: str = ""      # 到达机场英文名（回退用）
    url: str = ""           # Google Flights 链接（订票用）
    book_url: str = ""      # 直达「这趟具体航班订票页」的深链（含各家实付价+下单入口）
    risks: list = field(default_factory=list)  # 风险标注（中文）

    def route_str(self) -> str:
        codes = [self.origin] + [s.to for s in self.segments]
        return "→".join(codes)

    def layover_str(self) -> str:
        return " / ".join(lv.label() for lv in self.layovers) if self.layovers else "直达"

    def layover_cn_str(self) -> str:
        return " · ".join(lv.city_cn() for lv in self.layovers) if self.layovers else ""

    def dep_time_str(self) -> str:
        return _hm(self.dep_time)

    def arr_time_str(self) -> str:
        return _hm(self.arr_time)

    def total_str(self) -> str:
        return _dur(self.total_min)

    def stops_str(self) -> str:
        return "直达" if self.stops == 0 else f"{self.stops}次中转"

    def airlines_str(self) -> str:
        return "、".join(self.airlines) if self.airlines else "-"

    def origin_cn(self) -> str:
        return airports.cn_name(self.origin, self.origin_name)

    def dest_cn(self) -> str:
        return airports.cn_name(self.dest, self.dest_name)

    def dep_date_label(self) -> str:
        return date_label(self.dep_date)

    def day_offset(self) -> int:
        """到达日相对出发日的天数差（跨日 +1/+2）。"""
        a, b = _to_date(self.dep_date), _to_date(self.arr_date)
        if a and b:
            return (b - a).days
        return 0


@dataclass
class RouteResult:
    """某条航线本次检查的最优结果。"""
    route_key: str
    route_label: str
    primary: bool
    best: Optional[Itinerary]
    checked_pairs: int = 0
    found: int = 0
    error: Optional[str] = None


@dataclass
class CheckResult:
    """一次完整检查的聚合结果。"""
    timestamp: float
    routes: list                       # list[RouteResult]
    overall_best: Optional[Itinerary]  # 参与提醒的最低价
    trend_baseline: Optional[int]      # 趋势基准（同货币）
    prev_min: Optional[int]            # 本次之前的历史最低
    alert_reasons: list = field(default_factory=list)
    trend_source: Optional[str] = None  # "history" / "travelpayouts" / None
    return_leg: Optional[Itinerary] = None  # 返程参考行程（展示用，不入库）
    # 「拆票」方案：去/返各买一张单程，合计可能更便宜（尤其廉航）。
    # 仅当合计价明显低于整张往返票时才有意义；两段是分开的票，需各自下单。
    split_price: Optional[int] = None       # 两段单程合计价
    split_out: Optional[Itinerary] = None   # 去程单程
    split_ret: Optional[Itinerary] = None   # 返程单程
    split_label: str = ""                   # 航线标签（如「北京大兴 → 悉尼」）

    @property
    def split_cheaper(self) -> bool:
        """拆票是否比整张往返票更便宜（有效且更低）。"""
        return (
            self.split_price is not None
            and self.overall_best is not None
            and self.split_price < self.overall_best.price
        )

    @property
    def alerted(self) -> bool:
        return bool(self.alert_reasons)
