"""设置对话框。"""
from __future__ import annotations

from datetime import date as _date, timedelta as _timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


SEATS = [
    ("economy", "经济舱"),
    ("premium-economy", "超级经济舱"),
    ("business", "公务舱"),
    ("first", "头等舱"),
]


def _to_qdate(s: str) -> QDate:
    y, m, d = (int(x) for x in s.split("-"))
    return QDate(y, m, d)


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self.setWindowTitle("机票监控 · 设置")
        self.setMinimumWidth(380)
        self._build()

    def _cfg_date(self, key: str, fallback_key: str) -> QDate:
        return _to_qdate(self._cfg.get(key) or self._cfg.get(fallback_key))

    def _update_hint(self, *_args) -> None:
        def qd(widget) -> _date:
            d = widget.date()
            return _date(d.year(), d.month(), d.day())

        ds, de = qd(self.dep_start), qd(self.dep_end)
        rs, re = qd(self.ret_start), qd(self.ret_end)
        if de < ds:
            de = ds
        if re < rs:
            re = rs
        mn, mx = self.min_nights.value(), self.max_nights.value()

        cnt = 0
        a = ds
        while a <= de:
            b = rs
            while b <= re:
                nights = (b - a).days
                if mn <= nights <= mx:
                    cnt += 1
                b += _timedelta(days=1)
            a += _timedelta(days=1)

        cap = int(self._cfg.get("max_pairs_per_route", 24))
        if cnt == 0:
            self.hint.setText("⚠ 当前范围/天数下没有有效组合，请放宽范围或调整行程天数")
        elif cnt > cap:
            self.hint.setText(f"主航线约 {cnt} 个去/返日期组合；超过上限 {cap}，将自动均匀降采样")
        else:
            self.hint.setText(f"主航线将并发查询约 {cnt} 个去/返日期组合")

    def _build(self) -> None:
        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        root.addLayout(form)

        self.dep_start = QDateEdit(self._cfg_date("depart_start", "anchor_depart"))
        self.dep_end = QDateEdit(self._cfg_date("depart_end", "anchor_depart"))
        self.ret_start = QDateEdit(self._cfg_date("return_start", "anchor_return"))
        self.ret_end = QDateEdit(self._cfg_date("return_end", "anchor_return"))
        for d in (self.dep_start, self.dep_end, self.ret_start, self.ret_end):
            d.setDisplayFormat("yyyy-MM-dd")
            d.setCalendarPopup(True)
            d.dateChanged.connect(self._update_hint)
        form.addRow("去程范围 · 最早", self.dep_start)
        form.addRow("去程范围 · 最晚", self.dep_end)
        form.addRow("返程范围 · 最早", self.ret_start)
        form.addRow("返程范围 · 最晚", self.ret_end)

        self.min_nights = QSpinBox()
        self.min_nights.setRange(1, 60)
        self.min_nights.setValue(int(self._cfg.get("min_trip_days", 5)))
        self.min_nights.setSuffix(" 晚")
        self.max_nights = QSpinBox()
        self.max_nights.setRange(1, 90)
        self.max_nights.setValue(int(self._cfg.get("max_trip_days", 20)))
        self.max_nights.setSuffix(" 晚")
        for s in (self.min_nights, self.max_nights):
            s.valueChanged.connect(self._update_hint)
        form.addRow("行程最短", self.min_nights)
        form.addRow("行程最长", self.max_nights)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#3a7afe; font-size:11px;")
        form.addRow("", self.hint)

        self.seat = QComboBox()
        for code, name in SEATS:
            self.seat.addItem(name, code)
        idx = max(0, [c for c, _ in SEATS].index(self._cfg.get("seat", "economy")))
        self.seat.setCurrentIndex(idx)
        form.addRow("舱位", self.seat)

        self.adults = QSpinBox()
        self.adults.setRange(1, 9)
        self.adults.setValue(int(self._cfg["passengers"].get("adults", 1)))
        self.adults.setSuffix(" 人")
        form.addRow("成人数", self.adults)

        self.max_stops = QSpinBox()
        self.max_stops.setRange(0, 3)
        self.max_stops.setValue(int(self._cfg["max_stops"]))
        form.addRow("最大中转次数", self.max_stops)

        self.max_total = QSpinBox()
        self.max_total.setRange(6, 60)
        self.max_total.setValue(int(self._cfg["max_total_min"]) // 60)
        self.max_total.setSuffix(" 小时")
        form.addRow("全程上限", self.max_total)

        self.min_intl_conn = QSpinBox()
        self.min_intl_conn.setRange(45, 360)
        self.min_intl_conn.setSingleStep(15)
        self.min_intl_conn.setValue(int(self._cfg.get("hard_min_layover_intl_min", 75)))
        self.min_intl_conn.setSuffix(" 分钟")
        self.min_intl_conn.setToolTip(
            "跨境/国际中转（如经香港/首尔/新加坡）的最短衔接时间。\n"
            "低于此值的行程直接剔除——香港等地为独立关境，需重新过检、可能换航站楼，\n"
            "1 小时几乎必误机。建议 ≥75 分钟，稳妥可设 120。境内中转另按 45 分钟兜底。"
        )
        form.addRow("最短国际转机", self.min_intl_conn)

        self.pct = QSpinBox()
        self.pct.setRange(1, 50)
        self.pct.setValue(int(self._cfg["below_trend_pct"]))
        self.pct.setSuffix(" %")
        form.addRow("低于趋势提醒", self.pct)

        self.refresh = QSpinBox()
        self.refresh.setRange(10, 720)
        self.refresh.setSingleStep(5)
        cur_min = self._cfg.get("refresh_minutes")
        if cur_min is None:
            cur_min = int(self._cfg.get("refresh_hours", 3)) * 60
        self.refresh.setValue(int(cur_min))
        self.refresh.setSuffix(" 分钟")
        form.addRow("自动刷新间隔", self.refresh)

        self.trend_win = QSpinBox()
        self.trend_win.setRange(3, 60)
        self.trend_win.setValue(int(self._cfg.get("trend_window_days", 14)))
        self.trend_win.setSuffix(" 天")
        form.addRow("趋势基准窗口", self.trend_win)

        self.chart_days = QSpinBox()
        self.chart_days.setRange(7, 180)
        self.chart_days.setSingleStep(1)
        self.chart_days.setValue(int(self._cfg.get("chart_days", 60)))
        self.chart_days.setSuffix(" 天")
        self.chart_days.setToolTip("价格曲线按天聚合，每天一个点（当天最低价）；此处设置曲线显示多少天")
        form.addRow("价格曲线跨度", self.chart_days)

        self.token = QLineEdit(self._cfg.get("travelpayouts_token", ""))
        self.token.setPlaceholderText("可留空；默认用自建历史算趋势，填了则用其更广口径")
        form.addRow("Travelpayouts token", self.token)

        self.sound = QCheckBox("触发时播放提示音")
        self.sound.setChecked(bool(self._cfg["sound"]))
        root.addWidget(self.sound)

        self.autostart = QCheckBox("开机自启")
        self.autostart.setChecked(bool(self._cfg["autostart"]))
        root.addWidget(self.autostart)

        note = QLabel("提示：趋势基准由插件自建历史价自动计算，无需 token；监控航线可在 config.json 中编辑。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._update_hint()

    def apply_to(self, cfg: dict) -> dict:
        """把界面上的值写回 cfg（原地修改并返回）。"""
        ds = self.dep_start.date().toString("yyyy-MM-dd")
        de = self.dep_end.date().toString("yyyy-MM-dd")
        rs = self.ret_start.date().toString("yyyy-MM-dd")
        re = self.ret_end.date().toString("yyyy-MM-dd")
        # 起点晚于终点时自动纠正
        if de < ds:
            de = ds
        if re < rs:
            re = rs
        cfg["depart_start"], cfg["depart_end"] = ds, de
        cfg["return_start"], cfg["return_end"] = rs, re
        cfg["min_trip_days"] = self.min_nights.value()
        cfg["max_trip_days"] = self.max_nights.value()
        # 同步旧锚点字段（兼容）：用范围起点
        cfg["anchor_depart"], cfg["anchor_return"] = ds, rs
        cfg["seat"] = self.seat.currentData()
        cfg["passengers"]["adults"] = self.adults.value()
        cfg["max_stops"] = self.max_stops.value()
        cfg["max_total_min"] = self.max_total.value() * 60
        cfg["hard_min_layover_intl_min"] = self.min_intl_conn.value()
        cfg["below_trend_pct"] = self.pct.value()
        cfg["refresh_minutes"] = self.refresh.value()
        cfg["trend_window_days"] = self.trend_win.value()
        cfg["chart_days"] = self.chart_days.value()
        cfg["travelpayouts_token"] = self.token.text().strip()
        cfg["sound"] = self.sound.isChecked()
        cfg["autostart"] = self.autostart.isChecked()
        return cfg
