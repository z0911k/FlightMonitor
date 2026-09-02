"""检查周期编排：日期网格 → 抓取 → 筛选 → 选最优 → 存库 → 趋势 → 提醒判定。"""
from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from . import deeplink, storage
from .alerts import decide_alerts
from .filters import annotate_and_filter, arrives_by
from .models import CheckResult, Itinerary, RouteResult
from .sources import TravelpayoutsClient, fetch_one_way, fetch_route_pair

ProgressCb = Optional[Callable[[int, int, str], None]]


def _parse_d(date_str: str):
    return datetime.strptime(date_str, "%Y-%m-%d").date()


def _daterange(start: str, end: str) -> list[str]:
    """闭区间 [start, end] 内逐日展开为 'YYYY-MM-DD' 列表。"""
    d0, d1 = _parse_d(start), _parse_d(end)
    if d1 < d0:
        d1 = d0
    out, d = [], d0
    while d <= d1:
        out.append(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    return out


def _range_bounds(cfg: dict) -> tuple:
    """取去/返日期范围；缺失时回退旧的锚点字段（向后兼容）。"""
    dep_s = cfg.get("depart_start") or cfg.get("anchor_depart")
    dep_e = cfg.get("depart_end") or dep_s
    ret_s = cfg.get("return_start") or cfg.get("anchor_return")
    ret_e = cfg.get("return_end") or ret_s
    return dep_s, dep_e, ret_s, ret_e


def date_pairs_for_route(cfg: dict, route: dict) -> list[tuple]:
    """按用户设定的「去程范围 × 返程范围」生成 (去程, 返程) 日期对。

    - flex 航线：范围内做笛卡尔组合；否则只取范围起点各一天。
    - 用「行程天数 min/max」剔除过短/过长（避免出现离谱的早返程），并按上限降采样防组合爆炸。
    """
    dep_s, dep_e, ret_s, ret_e = _range_bounds(cfg)
    min_n = int(cfg.get("min_trip_days", 1))
    max_n = int(cfg.get("max_trip_days", 3650))
    cap = max(1, int(cfg.get("max_pairs_per_route", 24)))

    if route.get("flex"):
        deps, rets = _daterange(dep_s, dep_e), _daterange(ret_s, ret_e)
    else:
        deps, rets = [dep_s], [ret_s]

    pairs = []
    for a in deps:
        for b in rets:
            nights = (_parse_d(b) - _parse_d(a)).days
            if nights < max(1, min_n) or nights > max_n:
                continue
            pairs.append((a, b))
    pairs = sorted(set(pairs))

    if not pairs:  # 过度约束时兜底：范围内任取一个合法组合
        for a in deps:
            for b in rets:
                if _parse_d(b) > _parse_d(a):
                    pairs = [(a, b)]
                    break
            if pairs:
                break

    if len(pairs) > cap:  # 组合过多时均匀降采样（保留分布，控住查询量）
        step = len(pairs) / cap
        keep = sorted({int(i * step) for i in range(cap)})
        pairs = [pairs[i] for i in keep]
    return pairs


def _total_pairs(cfg: dict) -> int:
    return sum(len(date_pairs_for_route(cfg, r)) for r in cfg["routes"])


def _fetch_task(route: dict, dep: str, ret: str, cfg: dict) -> dict:
    """单个 (航线, 去程, 返程) 抓取任务，供线程池并发调用。"""
    origin, dest = route["origin"], route["dest"]
    label = route.get("label", f"{origin}-{dest}")
    try:
        time.sleep(random.uniform(0.0, 0.4))  # 轻微错峰，避免瞬时并发爆发
        itins = fetch_route_pair(origin, dest, dep, ret, cfg, route_label=label)
        kept = annotate_and_filter(itins, cfg)
        return {"route_key": f"{origin}-{dest}", "kept": kept, "err": None}
    except Exception as e:  # noqa: BLE001
        return {"route_key": f"{origin}-{dest}", "kept": [], "err": f"{type(e).__name__}: {e}"}


def _oneway_tasks(cfg: dict) -> tuple:
    """收集主航线的去/返「单程」查询任务（按机场对+日期去重）。"""
    out_tasks, ret_tasks = set(), set()
    for route in cfg["routes"]:
        if not route.get("primary"):
            continue
        origin, dest = route["origin"], route["dest"]
        label = route.get("label", f"{origin}-{dest}")
        pairs = date_pairs_for_route(cfg, route)
        for d in sorted({a for (a, _) in pairs}):
            out_tasks.add((origin, dest, d, label))
        for r in sorted({b for (_, b) in pairs}):
            ret_tasks.add((dest, origin, r, label))
    return out_tasks, ret_tasks


def _split_ticket_scan(cfg: dict, progress_cb: ProgressCb = None):
    """拆票扫描：去/返各买一张单程，找最便宜的「合计」组合。

    返回 (total, out_itin, ret_itin, route_label) 或 None。两段是分开的票、需各自下单，
    故仅作为「往返票之外的省钱提示」，不替代整张往返票。
    """
    out_tasks, ret_tasks = _oneway_tasks(cfg)
    jobs = [("out",) + t for t in out_tasks] + [("ret",) + t for t in ret_tasks]
    if not jobs:
        return None

    def _one(kind, o, d, dt, lbl):
        try:
            time.sleep(random.uniform(0.0, 0.4))
            kept = annotate_and_filter(fetch_one_way(o, d, dt, cfg, route_label=lbl), cfg)
            return kind, (o, d, dt), (kept[0] if kept else None)
        except Exception:  # noqa: BLE001
            return kind, (o, d, dt), None

    out_best: dict[tuple, Itinerary] = {}
    ret_best: dict[tuple, Itinerary] = {}
    workers = max(1, min(int(cfg.get("max_concurrency", 4)), len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, *j) for j in jobs]
        for fut in as_completed(futs):
            kind, key, best = fut.result()
            if best is None:
                continue
            (out_best if kind == "out" else ret_best)[key] = best

    best_split = None
    for route in cfg["routes"]:
        if not route.get("primary"):
            continue
        origin, dest = route["origin"], route["dest"]
        label = route.get("label", f"{origin}-{dest}")
        for (dep, ret) in date_pairs_for_route(cfg, route):
            o_it = out_best.get((origin, dest, dep))
            r_it = ret_best.get((dest, origin, ret))
            if o_it is None or r_it is None:
                continue
            total = o_it.price + r_it.price
            if best_split is None or total < best_split[0]:
                best_split = (total, o_it, r_it, label)
    return best_split


def scan_split(cfg: dict, progress_cb: ProgressCb = None):
    """公开入口：单独跑一次拆票扫描（供后台线程在往返结果显示后异步补齐）。

    返回 (total, out_itin, ret_itin, route_label) 或 None。
    """
    return _split_ticket_scan(cfg, progress_cb)


def _dedupe_cands(itins: list, topk: int) -> list:
    """取若干最便宜、去重后的候选去程（同一去/返日期+出发时刻+价格只留一条）。"""
    seen, out = set(), []
    for it in sorted(itins, key=lambda x: x.price):
        key = (it.depart_date, it.return_date, tuple(it.dep_time or ()), it.price)
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
        if len(out) >= topk:
            break
    return out


def _feasible_primary(cfg: dict, agg: dict, deadline: str, progress_cb: ProgressCb = None):
    """对每条主航线的若干最便宜候选去程，联网抓「选返程」页，只在**能按时落地**
    （返程落地 ≤ arrive_home_by）的返程里挑最便宜的，得到该场次诚实的往返总价。

    这样可避免「头条便宜价其实靠一趟落地已是上班日的超长中转返程」的假象。

    返回:
      feasible: dict[route_key] -> (itin[价已改写为诚实往返总价、已设 book_url], return_leg)
      excluded: set[route_key]   # 抓到了返程但没有能按时落地的 → 该出发场次赶不回来，剔除
    （「选返程」页联网/解析失败的候选不计入 excluded，避免误杀，交由 run_check 回退处理。）
    """
    topk = max(1, int(cfg.get("feasible_topk", 3)))
    jobs = []
    for route in cfg["routes"]:
        if not route.get("primary"):
            continue
        rk = f'{route["origin"]}-{route["dest"]}'
        for c in _dedupe_cands(agg.get(rk, {}).get("cands", []), topk):
            jobs.append((rk, c))
    if not jobs:
        return {}, set()

    if progress_cb:
        progress_cb(0, len(jobs), "校验返程能否按时回…")

    def _pair(rk, cand):
        try:
            time.sleep(random.uniform(0.0, 0.3))
            opts = deeplink.paired_return_options(cand, cfg)
        except Exception:  # noqa: BLE001
            opts = None
        return rk, cand, opts

    per_route: dict[str, dict] = {}
    workers = max(1, min(int(cfg.get("max_concurrency", 4)), len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_pair, rk, c) for (rk, c) in jobs]
        for fut in as_completed(futs):
            rk, cand, opts = fut.result()
            slot = per_route.setdefault(rk, {"best": None, "had_opts": False})
            if not opts:
                continue
            slot["had_opts"] = True
            feas = [o for o in opts if arrives_by(o, deadline)] if deadline else opts
            if not feas:
                continue
            ret = min(feas, key=lambda x: x.price)
            total = ret.price  # 「选返程」页每条 price 即往返总价
            if slot["best"] is None or total < slot["best"][0]:
                slot["best"] = (total, cand, ret)

    feasible: dict[str, tuple] = {}
    excluded: set[str] = set()
    for rk, slot in per_route.items():
        if slot["best"] is not None:
            total, itin, ret = slot["best"]
            itin.price = int(total)
            try:
                itin.book_url = (deeplink.booking_url_for(itin, ret, cfg)
                                 or deeplink.outbound_selected_url(itin, cfg))
            except Exception:  # noqa: BLE001
                itin.book_url = ""
            ret = (annotate_and_filter([ret], cfg) or [ret])[0]
            feasible[rk] = (itin, ret)
        elif slot["had_opts"]:
            excluded.add(rk)
    return feasible, excluded


def run_check(cfg: dict, progress_cb: ProgressCb = None, do_split: bool = True) -> CheckResult:
    storage.init_db()

    per_route_best: dict[str, Itinerary] = {}
    route_results: list[RouteResult] = []
    primary_bests: list[Itinerary] = []

    # 组装所有 (航线, 去程, 返程) 任务，并发抓取（大幅缩短一次刷新的耗时）
    tasks: list[tuple] = []
    for route in cfg["routes"]:
        for (dep, ret) in date_pairs_for_route(cfg, route):
            tasks.append((route, dep, ret))
    total = len(tasks)
    workers = max(1, min(int(cfg.get("max_concurrency", 4)), total or 1))

    agg: dict[str, dict] = {}
    done = 0
    if progress_cb:
        progress_cb(0, total, f"并发查询中 0/{total}")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_fetch_task, r, d, rt, cfg) for (r, d, rt) in tasks]
        for fut in as_completed(futs):
            res = fut.result()
            rk = res["route_key"]
            a = agg.setdefault(rk, {"best": None, "found": 0, "checked": 0, "err": None, "cands": []})
            a["checked"] += 1
            a["found"] += len(res["kept"])
            if res["kept"]:
                a["cands"].extend(res["kept"])
                if a["best"] is None or res["kept"][0].price < a["best"].price:
                    a["best"] = res["kept"][0]
            if res["err"]:
                a["err"] = res["err"]
            done += 1
            if progress_cb:
                progress_cb(done, total, f"并发查询中 {done}/{total}")

    # 诚实定价：对主航线的最便宜候选去程，只保留「能按 arrive_home_by 落地」的返程，
    # 用其往返总价参与比价——避免头条价靠一趟落地已是上班日的超长中转返程。
    deadline = cfg.get("arrive_home_by", "")
    feasible_map, excluded = _feasible_primary(cfg, agg, deadline, progress_cb)

    # 按原航线顺序汇总
    return_legs: dict[str, Itinerary] = {}
    for route in cfg["routes"]:
        route_key = f'{route["origin"]}-{route["dest"]}'
        label = route.get("label", route_key)
        a = agg.get(route_key, {"best": None, "found": 0, "checked": 0, "err": None, "cands": []})
        primary = bool(route.get("primary"))
        best = a["best"]
        if primary:
            if route_key in feasible_map:
                best, rleg = feasible_map[route_key]
                return_legs[route_key] = rleg
            elif route_key in excluded:
                best = None  # 有返程但都赶不回来 → 该场次剔除
            # 否则（选返程页联网失败）：保留原始最便宜 best，交由下方返程明细回退兜底
        route_results.append(
            RouteResult(route_key, label, primary, best, a["checked"], a["found"], a["err"])
        )
        if best is not None:
            per_route_best[route_key] = best
            if primary:
                primary_bests.append(best)

    overall_best = min(primary_bests, key=lambda x: x.price) if primary_bests else None

    # 返程明细 + 直达订票深链。若诚实定价阶段已配好可按时落地的返程，直接复用；
    # 否则（该场次走了原始回退）联网补配「能按时落地」的返程与订票深链，仍失败再优雅回退。
    return_leg: Optional[Itinerary] = None
    if overall_best is not None:
        return_leg = return_legs.get(overall_best.route_key)
    if overall_best is not None and return_leg is None and overall_best.return_date:
        if progress_cb:
            progress_cb(total, total, "获取返程明细…")
        try:
            book_url, paired_ret = deeplink.booking_url_with_return(overall_best, cfg)
        except Exception:  # noqa: BLE001
            book_url, paired_ret = "", None
        if book_url:
            overall_best.book_url = book_url
        if paired_ret is not None:
            paired_ret = (annotate_and_filter([paired_ret], cfg) or [paired_ret])[0]
            return_leg = paired_ret
        if not overall_best.book_url:
            # 联网构造失败：退回「预选去程」的本地链接（去程已锁定，页面直接选返程）
            try:
                overall_best.book_url = deeplink.outbound_selected_url(overall_best, cfg)
            except Exception:  # noqa: BLE001
                overall_best.book_url = ""
        if return_leg is None:
            # 返程展示回退：原单程查询取「能按时落地」的代表性返程
            try:
                rets = fetch_one_way(
                    overall_best.dest,
                    overall_best.origin,
                    overall_best.return_date,
                    cfg,
                    route_label=overall_best.route_label,
                )
                rets = annotate_and_filter(rets, cfg)
                if deadline:
                    rets = [r for r in rets if arrives_by(r, deadline)]
                if rets:
                    return_leg = min(rets, key=lambda x: (x.stops, x.total_min))
            except Exception:  # noqa: BLE001
                return_leg = None

    # 趋势基准：优先 Travelpayouts（若配置 token），否则用自建历史中位数（零 token）
    baseline: Optional[int] = None
    trend_source: Optional[str] = None
    tp = TravelpayoutsClient(cfg.get("travelpayouts_token", ""), cfg.get("travelpayouts_currency", "cny"))
    if overall_best is not None and tp.enabled:
        baseline = tp.route_baseline(
            overall_best.origin,
            overall_best.dest,
            overall_best.depart_date[:7],
            overall_best.return_date[:7],
        )
        if baseline:
            trend_source = "travelpayouts"
    if baseline is None:
        # 用当前周期之前的历史（尚未写库）算中位数
        local = storage.median_over_days(
            storage.OVERALL,
            int(cfg.get("trend_window_days", 14)),
            int(cfg.get("trend_min_samples", 5)),
        )
        if local:
            baseline = local
            trend_source = "history"

    # 记录前先取历史最低（用于「新低」判定）
    prev_min = storage.all_time_min()
    ts = time.time()
    storage.record_cycle(ts, per_route_best, overall_best)

    reasons = decide_alerts(
        overall_best.price if overall_best else None,
        prev_min,
        baseline,
        cfg,
        storage.last_alert(),
    )
    if reasons and overall_best is not None:
        storage.log_alert(overall_best.price, "; ".join(reasons), ts)

    # 拆票扫描：去/返各买一张单程，合计可能更便宜（尤其廉航，往返搜索常不列出）。
    # do_split=False 时跳过（后台线程会在往返结果先显示后，另行调用 scan_split 异步补齐，避免拖慢头条价格）。
    split_price = split_out = split_ret = None
    split_label = ""
    if do_split:
        try:
            if progress_cb:
                progress_cb(total, total, "扫描拆票组合…")
            bs = _split_ticket_scan(cfg, progress_cb)
            if bs is not None:
                split_price, split_out, split_ret, split_label = bs
        except Exception:  # noqa: BLE001 — 拆票是增强项，失败不影响主流程
            split_price = split_out = split_ret = None

    if progress_cb:
        progress_cb(total, total, "完成")

    return CheckResult(
        timestamp=ts,
        routes=route_results,
        overall_best=overall_best,
        trend_baseline=baseline,
        prev_min=prev_min,
        alert_reasons=reasons,
        trend_source=trend_source,
        return_leg=return_leg,
        split_price=split_price,
        split_out=split_out,
        split_ret=split_ret,
        split_label=split_label,
    )
