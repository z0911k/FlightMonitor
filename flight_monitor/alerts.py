"""提醒决策引擎（不设死价：新低 / 明显低于趋势）。"""
from __future__ import annotations

import time
from typing import Optional


def decide_alerts(
    price: Optional[int],
    prev_min: Optional[int],
    trend_baseline: Optional[int],
    cfg: dict,
    last_alert: Optional[tuple] = None,
) -> list[str]:
    """返回触发原因列表（空列表表示不提醒）。

    参数
    ----
    price:          本次的总最低价
    prev_min:       本次之前的历史最低价（None 表示首次，无基线不提醒新低）
    trend_baseline: Travelpayouts 近月价格基准（None 表示不可用）
    last_alert:     (ts, price) 最近一次提醒，用于去抖
    """
    if price is None:
        return []

    reasons: list[str] = []

    # 1) 监控期新低
    if prev_min is not None and price < prev_min:
        reasons.append(f"刷新监控期新低（此前最低 ¥{prev_min:,}）")

    # 2) 明显低于近月趋势
    pct = cfg.get("below_trend_pct", 10)
    if trend_baseline:
        threshold = trend_baseline * (1 - pct / 100.0)
        if price <= threshold:
            actual = round((trend_baseline - price) / trend_baseline * 100)
            reasons.append(f"比近月趋势(¥{trend_baseline:,})低约 {actual}%")

    if not reasons:
        return []

    # 3) 去抖：窗口内不重复提醒「同等或更贵」的价格
    if last_alert is not None:
        last_ts, last_price = last_alert
        window = cfg.get("alert_debounce_hours", 12) * 3600
        if (time.time() - last_ts) < window and last_price is not None and price >= last_price:
            return []

    return reasons
