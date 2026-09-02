"""IATA 机场代码 → 中文名称映射（覆盖京澳航线及常见中转枢纽）。

未知代码时回退到解析出的英文名，再回退到代码本身。
"""
from __future__ import annotations

# 代码 -> 中文（尽量用「城市+机场」简称，便于一眼看懂）
IATA_CN: dict[str, str] = {
    # 出发地（北京）
    "PEK": "北京首都",
    "PKX": "北京大兴",
    "NAY": "北京南苑",
    "TSN": "天津滨海",
    "SJW": "石家庄正定",
    # 目的地（澳大利亚）
    "SYD": "悉尼",
    "CBR": "堪培拉",
    "MEL": "墨尔本",
    "BNE": "布里斯班",
    "PER": "珀斯",
    "ADL": "阿德莱德",
    "OOL": "黄金海岸",
    "CNS": "凯恩斯",
    # 中国内地枢纽
    "CAN": "广州",
    "PVG": "上海浦东",
    "SHA": "上海虹桥",
    "SZX": "深圳",
    "CTU": "成都双流",
    "TFU": "成都天府",
    "CKG": "重庆",
    "XMN": "厦门",
    "KMG": "昆明",
    "WUH": "武汉",
    "HGH": "杭州",
    "NKG": "南京",
    "TAO": "青岛",
    "CSX": "长沙",
    "XIY": "西安",
    "FOC": "福州",
    "HAK": "海口",
    "SYX": "三亚",
    # 港澳台
    "HKG": "香港",
    "MFM": "澳门",
    "TPE": "台北桃园",
    "KHH": "高雄",
    # 亚太中转枢纽
    "SIN": "新加坡",
    "KUL": "吉隆坡",
    "BKK": "曼谷素万那普",
    "DMK": "曼谷廊曼",
    "HAN": "河内",
    "SGN": "胡志明市",
    "MNL": "马尼拉",
    "CEB": "宿务",
    "CGK": "雅加达",
    "DPS": "巴厘岛",
    "ICN": "首尔仁川",
    "GMP": "首尔金浦",
    "NRT": "东京成田",
    "HND": "东京羽田",
    "KIX": "大阪关西",
    "NGO": "名古屋",
    "AKL": "奥克兰",
    "CHC": "基督城",
    # 中东/其它常见转机点
    "DOH": "多哈",
    "DXB": "迪拜",
    "AUH": "阿布扎比",
    "IST": "伊斯坦布尔",
    "KUL2": "吉隆坡",
}

# 机场代码 -> 所属城市中文（用于中转地按城市显示）
IATA_CITY_CN: dict[str, str] = {
    "PEK": "北京", "PKX": "北京", "NAY": "北京",
    "TSN": "天津", "SJW": "石家庄",
    "SYD": "悉尼", "CBR": "堪培拉", "MEL": "墨尔本", "BNE": "布里斯班",
    "PER": "珀斯", "ADL": "阿德莱德", "OOL": "黄金海岸", "CNS": "凯恩斯",
    "CAN": "广州", "PVG": "上海", "SHA": "上海", "SZX": "深圳",
    "CTU": "成都", "TFU": "成都", "CKG": "重庆", "XMN": "厦门",
    "KMG": "昆明", "WUH": "武汉", "HGH": "杭州", "NKG": "南京",
    "TAO": "青岛", "CSX": "长沙", "XIY": "西安",
    "HKG": "香港", "MFM": "澳门", "TPE": "台北", "KHH": "高雄",
    "SIN": "新加坡", "KUL": "吉隆坡", "BKK": "曼谷", "DMK": "曼谷",
    "HAN": "河内", "SGN": "胡志明", "MNL": "马尼拉", "CEB": "宿务",
    "CGK": "雅加达", "DPS": "巴厘岛", "ICN": "首尔", "GMP": "首尔",
    "NRT": "东京", "HND": "东京", "KIX": "大阪", "NGO": "名古屋",
    "AKL": "奥克兰", "CHC": "基督城",
    "DOH": "多哈", "DXB": "迪拜", "AUH": "阿布扎比", "IST": "伊斯坦布尔",
}


# 机场代码 -> 关境/入境地区（用于判断中转是否「跨境」：港澳台与内地互为独立关境，
# 跨境中转往往要重新过检/边检、可能换航站楼，紧凑衔接风险明显更高）。
IATA_REGION: dict[str, str] = {
    # 中国内地
    "PEK": "CN", "PKX": "CN", "NAY": "CN", "CAN": "CN", "PVG": "CN", "SHA": "CN",
    "TSN": "CN", "SJW": "CN",
    "SZX": "CN", "CTU": "CN", "TFU": "CN", "CKG": "CN", "XMN": "CN", "KMG": "CN",
    "WUH": "CN", "HGH": "CN", "NKG": "CN", "TAO": "CN", "CSX": "CN", "XIY": "CN",
    "FOC": "CN", "HAK": "CN", "SYX": "CN",
    # 港澳台（各自独立关境）
    "HKG": "HK", "MFM": "MO", "TPE": "TW", "KHH": "TW",
    # 澳大利亚
    "SYD": "AU", "CBR": "AU", "MEL": "AU", "BNE": "AU", "PER": "AU", "ADL": "AU",
    "OOL": "AU", "CNS": "AU",
    # 亚太/中东其它枢纽
    "SIN": "SG", "KUL": "MY", "BKK": "TH", "DMK": "TH", "HAN": "VN", "SGN": "VN",
    "MNL": "PH", "CEB": "PH", "CGK": "ID", "DPS": "ID", "ICN": "KR", "GMP": "KR",
    "NRT": "JP", "HND": "JP", "KIX": "JP", "NGO": "JP", "AKL": "NZ", "CHC": "NZ",
    "DOH": "QA", "DXB": "AE", "AUH": "AE", "IST": "TR",
}


def region_of(code: str):
    """机场所属关境/地区代码；未知返回 None。"""
    if not code:
        return None
    return IATA_REGION.get(code.strip().upper())


def is_cross_border(layover_code: str, origin_code: str) -> bool:
    """中转是否为「跨境中转」（相对出发地而言，需另经边检/关境）。

    港澳台与内地互不相同，均视为跨境。中转地未知时保守按跨境处理（从严标注）。
    """
    lo = region_of(layover_code)
    if lo is None:
        return True
    return lo != region_of(origin_code)


def cn_name(code: str, en_fallback: str = "") -> str:
    """机场中文名；未知则用英文名，再退回代码。"""
    if not code:
        return en_fallback or "?"
    code = code.strip().upper()
    if code in IATA_CN:
        return IATA_CN[code]
    return en_fallback or code


def city_name(code: str, en_fallback: str = "") -> str:
    """中转地城市中文名；未知则回退英文/代码。"""
    if not code:
        return en_fallback or "?"
    code = code.strip().upper()
    if code in IATA_CITY_CN:
        return IATA_CITY_CN[code]
    if code in IATA_CN:
        return IATA_CN[code]
    return en_fallback or code
