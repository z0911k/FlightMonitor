"""数据源封装：Google Flights 实时价 + Travelpayouts 趋势兜底。"""
from __future__ import annotations

import statistics
import time
from typing import Optional

import requests
from fast_flights import (
    FlightQuery,
    Passengers,
    create_query,
    fetch_flights_html,
)
from fast_flights.exceptions import FlightsNotFound

from .models import Itinerary
from .parser import ParseError, parse_itineraries


# ---------------- Google Flights ----------------

def _passengers(cfg: dict) -> Passengers:
    p = cfg["passengers"]
    return Passengers(
        adults=p.get("adults", 1),
        children=p.get("children", 0),
        infants_in_seat=p.get("infants_in_seat", 0),
        infants_on_lap=p.get("infants_on_lap", 0),
    )


def build_query(origin: str, dest: str, depart_date: str, return_date: str, cfg: dict):
    return create_query(
        flights=[
            FlightQuery(date=depart_date, from_airport=origin, to_airport=dest),
            FlightQuery(date=return_date, from_airport=dest, to_airport=origin),
        ],
        seat=cfg.get("seat", "economy"),
        trip="round-trip",
        passengers=_passengers(cfg),
        language=cfg.get("language", "en-US"),
        currency=cfg.get("currency", "CNY"),
        max_stops=cfg.get("max_stops", 2),
    )


def fetch_route_pair(
    origin: str,
    dest: str,
    depart_date: str,
    return_date: str,
    cfg: dict,
    *,
    route_label: str = "",
    retries: int = 2,
) -> list[Itinerary]:
    """抓取并解析某一对(去程/返程)日期的往返结果。

    无结果返回 []；网络/解析异常会重试，最终失败抛出最后一个异常。
    """
    route_key = f"{origin}-{dest}"
    query = build_query(origin, dest, depart_date, return_date, cfg)
    url = query.url()
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            html = fetch_flights_html(query)
            itins = parse_itineraries(
                html,
                route_key=route_key,
                route_label=route_label or route_key,
                depart_date=depart_date,
                return_date=return_date,
                url=url,
            )
            return itins
        except FlightsNotFound:
            return []
        except (ParseError, requests.RequestException, Exception) as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            continue

    if last_err:
        raise last_err
    return []


def build_one_way_query(origin: str, dest: str, date: str, cfg: dict):
    return create_query(
        flights=[FlightQuery(date=date, from_airport=origin, to_airport=dest)],
        seat=cfg.get("seat", "economy"),
        trip="one-way",
        passengers=_passengers(cfg),
        language=cfg.get("language", "en-US"),
        currency=cfg.get("currency", "CNY"),
        max_stops=cfg.get("max_stops", 2),
    )


def fetch_one_way(
    origin: str,
    dest: str,
    date: str,
    cfg: dict,
    *,
    route_label: str = "",
    retries: int = 1,
) -> list[Itinerary]:
    """抓取并解析某一天的单程结果（用于展示返程明细）。无结果返回 []。"""
    route_key = f"{origin}-{dest}"
    query = build_one_way_query(origin, dest, date, cfg)
    url = query.url()
    last_err: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            html = fetch_flights_html(query)
            return parse_itineraries(
                html,
                route_key=route_key,
                route_label=route_label or route_key,
                depart_date=date,
                return_date="",
                url=url,
            )
        except FlightsNotFound:
            return []
        except (ParseError, requests.RequestException, Exception) as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
            continue
    if last_err:
        raise last_err
    return []


# ---------------- Travelpayouts 趋势兜底 ----------------

class TravelpayoutsClient:
    """Travelpayouts (Aviasales) Data API 客户端，用于历史/近月价格趋势基准。

    无 token 或请求失败时，所有方法返回 None（优雅降级）。
    """

    BASE = "https://api.travelpayouts.com"

    def __init__(self, token: str, currency: str = "cny"):
        self.token = (token or "").strip()
        self.currency = currency or "cny"

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def route_baseline(self, origin: str, dest: str, depart_month: str, return_month: str) -> Optional[int]:
        """返回该航线在给定去/返月份的往返价格基准（缓存价中位数）。"""
        if not self.enabled:
            return None
        prices = self._prices_for_dates(origin, dest, depart_month, return_month)
        if not prices:
            return None
        try:
            return int(statistics.median(prices))
        except statistics.StatisticsError:
            return None

    def _prices_for_dates(self, origin: str, dest: str, depart_at: str, return_at: str) -> list[int]:
        params = {
            "origin": origin,
            "destination": dest,
            "departure_at": depart_at,
            "return_at": return_at,
            "currency": self.currency,
            "sorting": "price",
            "one_way": "false",
            "limit": 30,
            "page": 1,
            "token": self.token,
        }
        try:
            r = requests.get(
                f"{self.BASE}/aviasales/v3/prices_for_dates",
                params=params,
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return []
        if not data.get("success", True):
            return []
        out = []
        for row in data.get("data", []) or []:
            price = row.get("price")
            if isinstance(price, (int, float)):
                out.append(int(price))
        return out
