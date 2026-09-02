"""航班筛选与风险标注（省钱优先：多为「标注」而非「硬排除」）。"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from . import airports
from .models import Itinerary


def arrival_date(itin: Itinerary) -> Optional[date]:
    """行程的落地日期（日历日）。优先用解析出的 arr_date；缺失时按
    出发日 + (出发时刻 + 全程时长) 跨过的自然日推算。无法确定则返回 None。"""
    ad = getattr(itin, "arr_date", None)
    if ad and len(ad) >= 3:
        try:
            return date(int(ad[0]), int(ad[1]), int(ad[2]))
        except (TypeError, ValueError):
            pass
    try:
        base = datetime.strptime(itin.depart_date, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    dep = itin.dep_time or (0, 0)
    h = int(dep[0]) if dep and dep[0] is not None else 0
    m = int(dep[1]) if dep and len(dep) > 1 and dep[1] is not None else 0
    total = itin.total_min or 0
    return base + timedelta(days=(h * 60 + m + total) // 1440)


def arrives_by(itin: Itinerary, deadline: str) -> bool:
    """返程行程是否在 deadline（含当日）之前落地。deadline 为空则不限制。
    落地日无法确定时不排除（宁可保留待人工判断，也不误杀）。"""
    if not deadline:
        return True
    try:
        limit = datetime.strptime(deadline, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return True
    ad = arrival_date(itin)
    if ad is None:
        return True
    return ad <= limit


def _is_redeye(t, start_hour: int, end_hour: int) -> bool:
    if not t or t[0] is None:
        return False
    try:
        h = int(t[0])
    except (TypeError, ValueError):
        return False
    # 夜间时段：[start, 24) ∪ [0, end)
    return h >= start_hour or h < end_hour


def evaluate_risks(itin: Itinerary, cfg: dict) -> list[str]:
    risks: list[str] = []
    beijing = {"PEK", "PKX"}

    distinct_airlines = len({a for a in itin.airlines if a})
    multi_airline = distinct_airlines >= 2

    dom_soft = cfg["min_layover_min"]                       # 境内中转偏紧阈值
    intl_warn = cfg.get("warn_layover_intl_min", 120)       # 跨境中转偏紧阈值
    self_transfer_min = cfg["min_layover_intl_min"]         # 自行中转（需自取行李重挂）建议阈值
    long_min = cfg["long_layover_min"]

    for lv in itin.layovers:
        code = (lv.code or "").upper()
        cross = airports.is_cross_border(code, itin.origin)
        loc = lv.label_cn()  # 例："香港 停1h05"

        if code in beijing:
            risks.append(f"京内换场({lv.city_cn()})")

        # 中转时长风险：区分「跨境/国际中转」与「境内中转」，前者要重新过检/可能换航站楼
        if cross:
            if lv.minutes < intl_warn:
                risks.append(f"国际转机偏紧 {loc}·建议≥2h(跨境需重新过检)")
        else:
            if lv.minutes < dom_soft:
                risks.append(f"中转偏紧 {loc}")

        if lv.minutes > long_min:
            risks.append(f"超长/过夜中转 {loc}")

        # 疑似自行中转且缓冲不足以从容入境/自取重挂行李
        if multi_airline and lv.minutes < self_transfer_min:
            risks.append(f"自行中转风险 {loc}·需自取行李重挂,建议≥3h")

    if multi_airline and not any("自行中转" in r for r in risks):
        risks.append("疑似自行中转(多航司/或分开出票)")

    if _is_redeye(itin.dep_time, cfg["redeye_start_hour"], cfg["redeye_end_hour"]):
        risks.append("红眼(夜间出发)")
    if _is_redeye(itin.arr_time, cfg["redeye_start_hour"], cfg["redeye_end_hour"]):
        risks.append("红眼(夜间到达)")

    return risks


def passes_hard_filters(itin: Itinerary, cfg: dict) -> bool:
    if itin.stops > cfg["max_stops"]:
        return False
    if itin.total_min and itin.total_min > cfg["max_total_min"]:
        return False
    hard_dom = cfg["hard_min_layover_min"]
    hard_intl = cfg.get("hard_min_layover_intl_min", hard_dom)
    for lv in itin.layovers:
        cross = airports.is_cross_border((lv.code or "").upper(), itin.origin)
        floor = hard_intl if cross else hard_dom
        if lv.minutes < floor:
            return False
    return True


def annotate_and_filter(itins: list[Itinerary], cfg: dict) -> list[Itinerary]:
    """硬筛选 + 风险标注，返回按价格升序的行程列表。

    单条行程解析/标注异常只跳过该条，绝不因一条脏数据丢弃整批（否则会误伤该
    日期组合里所有便宜票，看起来就像价格几小时不动）。
    """
    kept: list[Itinerary] = []
    for it in itins:
        try:
            if not passes_hard_filters(it, cfg):
                continue
        except Exception:  # noqa: BLE001 — 结构异常的行程直接跳过
            continue
        try:
            it.risks = evaluate_risks(it, cfg)
        except Exception:  # noqa: BLE001 — 风险标注失败不影响该行程参与比价
            it.risks = []
        kept.append(it)
    kept.sort(key=lambda x: x.price)
    return kept
