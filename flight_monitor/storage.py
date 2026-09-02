"""SQLite 价格历史存储。

- price_history：每次检查周期，记录各航线最优价 + 一条聚合「__overall__」最低价
- alert_log：提醒记录（用于去抖）
"""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from dataclasses import asdict
from typing import Optional

from .config import DB_PATH
from .models import Itinerary

OVERALL = "__overall__"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                route_key TEXT NOT NULL,
                depart_date TEXT,
                return_date TEXT,
                price INTEGER,
                stops INTEGER,
                total_min INTEGER,
                itin_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_ph_route_ts ON price_history(route_key, ts);

            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                price INTEGER,
                reason TEXT
            );
            """
        )


def _itin_row(conn, ts: float, route_key: str, itin: Optional[Itinerary]) -> None:
    if itin is None:
        return
    conn.execute(
        "INSERT INTO price_history (ts, route_key, depart_date, return_date, price, stops, total_min, itin_json)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            ts,
            route_key,
            itin.depart_date,
            itin.return_date,
            itin.price,
            itin.stops,
            itin.total_min,
            json.dumps(_itin_to_dict(itin), ensure_ascii=False),
        ),
    )


def _itin_to_dict(itin: Itinerary) -> dict:
    d = asdict(itin)
    return d


def record_cycle(
    timestamp: float,
    per_route_best: dict,
    overall_best: Optional[Itinerary],
) -> None:
    with _connect() as conn:
        for route_key, itin in per_route_best.items():
            _itin_row(conn, timestamp, route_key, itin)
        _itin_row(conn, timestamp, OVERALL, overall_best)


def all_time_min(route_key: str = OVERALL) -> Optional[int]:
    with _connect() as conn:
        row = conn.execute(
            "SELECT MIN(price) AS m FROM price_history WHERE route_key=? AND price IS NOT NULL",
            (route_key,),
        ).fetchone()
    return row["m"] if row and row["m"] is not None else None


def median_over_days(
    route_key: str = OVERALL,
    days: int = 14,
    min_samples: int = 5,
    before_ts: Optional[float] = None,
) -> Optional[int]:
    """最近 `days` 天内价格的中位数（自建趋势基准，零外部依赖）。

    样本不足 `min_samples` 条时返回 None（视为「积累中」）。
    `before_ts` 给定时只统计该时刻之前的记录（用于排除当前周期）。
    """
    end = before_ts if before_ts is not None else time.time()
    cutoff = end - days * 86400
    with _connect() as conn:
        if before_ts is not None:
            rows = conn.execute(
                "SELECT price FROM price_history WHERE route_key=? AND price IS NOT NULL"
                " AND ts>=? AND ts<?",
                (route_key, cutoff, before_ts),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT price FROM price_history WHERE route_key=? AND price IS NOT NULL AND ts>=?",
                (route_key, cutoff),
            ).fetchall()
    prices = [r["price"] for r in rows]
    if len(prices) < max(1, min_samples):
        return None
    try:
        return int(statistics.median(prices))
    except statistics.StatisticsError:
        return None


def recent(route_key: str = OVERALL, limit: int = 48) -> list[tuple]:
    """返回 [(ts, price), ...]，按时间升序，用于 sparkline。"""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ts, price FROM price_history WHERE route_key=? AND price IS NOT NULL"
            " ORDER BY ts DESC LIMIT ?",
            (route_key, limit),
        ).fetchall()
    return [(r["ts"], r["price"]) for r in reversed(rows)]


def series_since(route_key: str = OVERALL, days: int = 60,
                 max_points: int = 1500) -> list[tuple]:
    """最近 `days` 天内的原始采样点 [(ts, price), ...]，按时间升序。

    与 `recent`（固定取最近 N 个点）不同：这里按**时间窗**取，因此既能跨越多日
    （不会因抓取勤快就把昨天挤掉），又保留每天的价格波动细节（不做按天压平）。
    点数过多时按等距抽稀到 `max_points`，保证小窗绘制流畅。"""
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT ts, price FROM price_history"
            " WHERE route_key=? AND price IS NOT NULL AND ts>=?"
            " ORDER BY ts",
            (route_key, cutoff),
        ).fetchall()
    data = [(r["ts"], r["price"]) for r in rows]
    if len(data) > max_points:
        stride = len(data) / max_points
        picked = [data[int(i * stride)] for i in range(max_points)]
        if picked[-1] != data[-1]:
            picked[-1] = data[-1]  # 始终保留最新一点
        data = picked
    return data


def daily_series(route_key: str = OVERALL, days: int = 60) -> list[tuple]:
    """按自然日聚合的价格曲线：每天取当天最低价一个点，返回最近 `days` 天内的
    [(day_ts, min_price), ...]，按时间升序。用于长周期（默认 60 天）价格历史曲线——
    每天一个点，不会因抓取频繁而把昨天挤掉。"""
    cutoff = time.time() - days * 86400
    with _connect() as conn:
        rows = conn.execute(
            "SELECT MIN(ts) AS t, MIN(price) AS mn"
            " FROM price_history"
            " WHERE route_key=? AND price IS NOT NULL AND ts>=?"
            " GROUP BY strftime('%Y-%m-%d', ts, 'unixepoch', 'localtime')"
            " ORDER BY t",
            (route_key, cutoff),
        ).fetchall()
    return [(r["t"], r["mn"]) for r in rows]


def last_alert() -> Optional[tuple]:
    """返回最近一次提醒 (ts, price)，无则 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT ts, price FROM alert_log ORDER BY ts DESC LIMIT 1"
        ).fetchone()
    if row is None:
        return None
    return (row["ts"], row["price"])


def log_alert(price: int, reason: str, ts: Optional[float] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO alert_log (ts, price, reason) VALUES (?,?,?)",
            (ts if ts is not None else time.time(), price, reason),
        )
