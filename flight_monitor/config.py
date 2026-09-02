"""配置加载/保存与默认值。

配置文件与数据库都放在项目根目录下（可见、便于备份）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

# 数据目录：打包成 exe 后放在 exe 同级目录（可见、可写、便于备份）；
# 源码运行时用项目根目录 (…/FlightMonitor)。
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.json"
DB_PATH = BASE_DIR / "prices.db"

# ---- 默认配置 ----
DEFAULTS: dict = {
    # 乘客与查询
    "passengers": {"adults": 1, "children": 0, "infants_in_seat": 0, "infants_on_lap": 0},
    "seat": "economy",              # economy / premium-economy / business / first
    "currency": "CNY",
    "language": "en-US",

    # 日期范围（用户在设置里直接填「去程范围 / 返程范围」）
    "depart_start": "2026-09-24",   # 去程最早
    "depart_end": "2026-09-28",     # 去程最晚
    "return_start": "2026-10-05",   # 返程最早
    "return_end": "2026-10-08",     # 返程最晚
    "arrive_home_by": "",           # 硬约束：返程须在该日期(含)前落地出发地；空=不限（防「便宜票靠超长中转、落地已是上班日」）
    "min_trip_days": 5,             # 行程最短晚数（剔除过短/离谱早返程）
    "max_trip_days": 20,            # 行程最长晚数
    "max_pairs_per_route": 24,      # 单航线日期组合上限（超出则均匀降采样，防查询爆炸）
    # 旧字段（向后兼容，未设范围时回退用）
    "anchor_depart": "2026-09-26",
    "anchor_return": "2026-10-05",
    "buffer_days": 2,

    # 监控航线。flex=True 走灵活日期网格；primary=True 参与「总最低价」与提醒
    "routes": [
        {"origin": "PEK", "dest": "SYD", "label": "北京首都 → 悉尼", "flex": True, "primary": True},
        {"origin": "PKX", "dest": "SYD", "label": "北京大兴 → 悉尼", "flex": True, "primary": True},
        {"origin": "TSN", "dest": "SYD", "label": "天津滨海 → 悉尼", "flex": True, "primary": True},
        {"origin": "PEK", "dest": "CBR", "label": "北京 → 堪培拉(直达联程)", "flex": False, "primary": False},
    ],

    # 中转筛选（省钱优先：多为「标注」而非「硬排除」）
    "max_stops": 3,                 # 硬排除：超过则不看（放宽到 3 段中转，纳入更多组合比价）
    "max_total_min": 2400,          # 硬排除：全程 > 40h 不看（放宽，长中转靠风险标注提示）
    "hard_min_layover_min": 45,     # 硬排除：境内中转 < 45min 物理不可能
    "hard_min_layover_intl_min": 75,  # 硬排除：跨境/国际中转 < 75min（重新过检/换楼，几乎必误机）
    "min_layover_min": 90,          # 软标注：境内中转低于 1.5h 记「中转偏紧」
    "warn_layover_intl_min": 120,   # 软标注：跨境中转低于 2h 记「国际转机偏紧」
    "min_layover_intl_min": 180,    # 软标注：疑似自行中转/需自取重挂行李时，低于 3h 记风险
    "long_layover_min": 720,        # 软标注：> 12h 记「超长/过夜中转」

    # 提醒逻辑（不设死价）
    "below_trend_pct": 10,          # 低于趋势基准约 ≥10% 触发
    "alert_debounce_hours": 12,     # 去抖：N 小时内不重复提醒同等或更高价
    "redeye_start_hour": 23,        # 红眼时段（起飞/到达落在此区间标注）
    "redeye_end_hour": 6,

    # 趋势基准（自建历史，零 token）：取最近 N 天价格中位数；样本不足则「积累中」
    "trend_window_days": 14,
    "trend_min_samples": 5,
    "chart_days": 60,               # 价格曲线时间跨度（天）：按天聚合，每天一个点（当天最低价）

    # 运行
    "refresh_minutes": 30,          # 自动刷新间隔（分钟）：更勤快，能及时抓到降价
    "refresh_hours": 3,             # （兼容保留）旧字段；未设 refresh_minutes 时回退用它×60
    "request_gap_seconds": 2.0,     # （兼容保留）串行模式下的抓取间隔
    "max_concurrency": 4,           # 并发抓取线程数（4 条日期在 ~30s 内查完，而非 2-3 分钟）
    "sound": True,                  # 触发时播放提示音
    "window_layer": "bottom",       # 窗口层级：bottom(桌面挂件·不遮挡其它窗口) / normal / top
    "always_on_top": False,         # （兼容保留）等价于 window_layer=="top"
    "autostart": False,             # 开机自启
    "widget_scale": 1.0,            # 悬浮窗缩放（滚轮调节，自动记忆）

    # Travelpayouts（可选：填了就用它更广口径的近月基准，否则自动用自建历史）
    "travelpayouts_token": "",
    "travelpayouts_currency": "cny",
}


def load_config() -> dict:
    """读取配置，缺失字段用默认值补全。"""
    cfg = copy.deepcopy(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                user = json.load(f)
            _deep_update(cfg, user)
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_PATH)


def _deep_update(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
