"""桌面悬浮小窗 —— 精致深色玻璃卡片 + 去/返双程明细 + 平滑趋势图。

特性：
- 去程 / 返程分区展示（时刻、时长、中转、机场中文名）
- 鼠标滚轮缩放（0.7–2.2×，自动记忆）
- 三档窗口层级：置底（默认·不遮挡其它窗口，像桌面挂件）/ 普通 / 置顶
  置底采用 Win32 SetWindowPos(HWND_BOTTOM) + 定时重钉，参考「世界时钟」实现
"""
from __future__ import annotations

import ctypes
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QDesktopServices,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import airports, compare
from .models import CheckResult, date_label

# ---------------- Win32：置底/置顶层级控制（参考「世界时钟」桌面挂件） ----------------
_user32 = ctypes.windll.user32
_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOOLWINDOW = 0x00000080
_HWND_BOTTOM = 1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_BOTTOM = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE


def _win_send_to_bottom(hwnd: int) -> None:
    try:
        _user32.SetWindowPos(int(hwnd), _HWND_BOTTOM, 0, 0, 0, 0, _SWP_BOTTOM)
    except Exception:  # noqa: BLE001
        pass


def _win_set_toolwindow(hwnd: int) -> None:
    """仅隐藏任务栏/Alt-Tab（WS_EX_TOOLWINDOW）。

    刻意不加 WS_EX_NOACTIVATE：那会让交互式挂件的按钮点击在 Qt 里时灵时不灵
    （「世界时钟」是纯展示、无按钮才无所谓）。去掉它后按钮点击稳定送达。
    """
    try:
        ex = _user32.GetWindowLongW(int(hwnd), _GWL_EXSTYLE)
        ex = (ex | _WS_EX_TOOLWINDOW) & ~_WS_EX_NOACTIVATE
        _user32.SetWindowLongW(int(hwnd), _GWL_EXSTYLE, ex)
    except Exception:  # noqa: BLE001
        pass


# ---------------- 主题 ----------------
BG_TOP = "#1b1f2e"
BG_BOT = "#111420"
BORDER = "#2b3242"
FG = "#eef1f7"
MUTED = "#8892a8"
FAINT = "#5b6478"
GREEN = "#2fe0a6"
RED = "#ff6b81"
ACCENT = "#5b8cff"       # 去程色
ACCENT2 = "#7cc6ff"
RETURN_C = "#c58cff"     # 返程色
GOLD = "#ffce5c"
CHIP_BG = "#232a3b"

BASE_W = 372
SCALE_MIN, SCALE_MAX = 0.7, 2.2


def _pxf(scale: float, v: float) -> int:
    return max(1, round(v * scale))


def make_app_icon() -> QIcon:
    """程序化生成渐变圆角「¥」图标，免图标文件。"""
    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, 64, 64)
    g.setColorAt(0, QColor(ACCENT))
    g.setColorAt(1, QColor(GREEN))
    p.setBrush(QBrush(g))
    p.setPen(Qt.NoPen)
    p.drawRoundedRect(QRectF(4, 4, 56, 56), 16, 16)
    p.setPen(QPen(QColor("#ffffff")))
    p.setFont(QFont("Segoe UI", 30, QFont.Bold))
    p.drawText(pix.rect(), Qt.AlignCenter, "¥")
    p.end()
    return QIcon(pix)


def _smooth_path(pts: list[QPointF]) -> QPainterPath:
    """Catmull-Rom → 三次贝塞尔平滑折线。"""
    path = QPainterPath()
    path.moveTo(pts[0])
    n = len(pts)
    for i in range(1, n):
        p0 = pts[i - 1]
        p1 = pts[i]
        prev = pts[i - 2] if i >= 2 else p0
        nxt = pts[i + 1] if i + 1 < n else p1
        c1 = QPointF(p0.x() + (p1.x() - prev.x()) / 6.0, p0.y() + (p1.y() - prev.y()) / 6.0)
        c2 = QPointF(p1.x() - (nxt.x() - p0.x()) / 6.0, p1.y() - (nxt.y() - p0.y()) / 6.0)
        path.cubicTo(c1, c2, p1)
    return path


class Sparkline(QWidget):
    """平滑价格趋势折线：渐变填充 + 基准虚线 + 最低点 + 当前点光晕。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._prices: list[float] = []
        self._baseline: float | None = None
        self.setMinimumHeight(56)

    def set_data(self, prices, baseline=None) -> None:
        self._prices = [float(p) for p in prices if p is not None]
        self._baseline = float(baseline) if baseline else None
        self.update()

    def paintEvent(self, event):  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h, pad = self.width(), self.height(), 8

        if len(self._prices) < 2:
            p.setPen(QPen(QColor(FAINT)))
            p.setFont(QFont("Microsoft YaHei UI", 9))
            p.drawText(self.rect(), Qt.AlignCenter, "价格趋势积累中…")
            p.end()
            return

        vals = self._prices
        lo, hi = min(vals), max(vals)
        if self._baseline is not None:
            lo, hi = min(lo, self._baseline), max(hi, self._baseline)
        span = (hi - lo) or 1.0
        n = len(vals)
        dx = (w - 2 * pad) / (n - 1)

        def pt(i: int) -> QPointF:
            x = pad + dx * i
            y = h - pad - (vals[i] - lo) / span * (h - 2 * pad)
            return QPointF(x, y)

        pts = [pt(i) for i in range(n)]
        line = _smooth_path(pts)

        down = vals[-1] <= vals[0]
        col = QColor(GREEN if down else RED)

        fill = QPainterPath(line)
        fill.lineTo(QPointF(w - pad, h - pad))
        fill.lineTo(QPointF(pad, h - pad))
        fill.closeSubpath()
        grad = QLinearGradient(0, 0, 0, h)
        c0, c1 = QColor(col), QColor(col)
        c0.setAlpha(80)
        c1.setAlpha(0)
        grad.setColorAt(0, c0)
        grad.setColorAt(1, c1)
        p.fillPath(fill, QBrush(grad))

        if self._baseline is not None and lo <= self._baseline <= hi:
            by = h - pad - (self._baseline - lo) / span * (h - 2 * pad)
            pen = QPen(QColor(GOLD), 1, Qt.DashLine)
            pen.setDashPattern([3, 3])
            p.setPen(pen)
            p.drawLine(QPointF(pad, by), QPointF(w - pad, by))

        pen = QPen(col, 2.2)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(line)

        mi = vals.index(min(vals))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(GOLD))
        p.drawEllipse(pts[mi], 3.0, 3.0)

        last = pts[-1]
        glow = QColor(col)
        glow.setAlpha(55)
        p.setBrush(glow)
        p.drawEllipse(last, 6.5, 6.5)
        p.setBrush(col)
        p.drawEllipse(last, 3.4, 3.4)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(last, 1.4, 1.4)
        p.end()


class FloatingWidget(QWidget):
    """常驻桌面、置于最底层的无边框悬浮窗。滚轮缩放。"""

    refresh_requested = Signal()
    settings_requested = Signal()
    book_requested = Signal()
    hide_requested = Signal()
    scale_changed = Signal(float)
    window_deactivated = Signal()  # 失去激活（用户点了别的窗口）→ 供「召唤后自动沉底」

    def __init__(self, parent=None, scale: float = 1.0):
        super().__init__(parent)
        self._scale = min(SCALE_MAX, max(SCALE_MIN, float(scale or 1.0)))
        self._drag_off = None
        self._book_url = ""
        self._gf_url = ""                 # Google Flights 搜索页链接（订票菜单里备选）
        self._book_ob = None              # 当前最优行程（订票菜单标注「这趟航班」用）
        self._split_data = None           # (split_out, split_ret, split_price) 拆票更省时才有
        # 缓存渲染状态（缩放时重绘用）
        self._result: CheckResult | None = None
        self._spark_prices: list = []
        self._spark_baseline = None
        self._hist_price = None
        self._status_text = "就绪"
        self._busy = False
        self._cmp_route: tuple | None = None  # (origin, dest, dep, ret) 供比价菜单
        self._last_ts: float | None = None    # 最近一次成功查询时间（用于「X分钟前」）
        self._offline = False                 # 断网/抓取失败：展示最近价格并提示

        # 三档窗口层级：bottom（默认·桌面挂件，不遮挡其它窗口）/ normal / top
        # 置底通过 Win32 定时重钉到 Z 序最底，参考桌面「世界时钟」的实现
        self._layer = "bottom"
        self._hovering = False
        self._pin_timer: QTimer | None = None
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._build()
        self._apply_metrics()
        self._repaint_all()

    def set_layer(self, mode: str) -> None:
        """切换窗口层级：bottom / normal / top。

        - bottom：置底，像桌面挂件，不遮挡其它应用；用 SetWindowPos(HWND_BOTTOM) 定时重钉
        - normal：普通层级
        - top：钉在最上层，常驻可见
        切 flags 会让窗口短暂隐藏并可能丢失位置，故保存/恢复几何。
        """
        mode = mode if mode in ("bottom", "normal", "top") else "bottom"
        self._layer = mode
        geo = self.geometry()
        flags = Qt.FramelessWindowHint | Qt.Tool
        if mode == "top":
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setGeometry(geo)
        self.show()

        if self._pin_timer is not None:
            self._pin_timer.stop()

        if mode == "top":
            self.raise_()
        elif mode == "bottom":
            _win_set_toolwindow(int(self.winId()))
            self._pin_to_bottom()
            if self._pin_timer is None:
                self._pin_timer = QTimer(self)
                self._pin_timer.setSingleShot(False)
                self._pin_timer.timeout.connect(self._pin_to_bottom)
            self._pin_timer.start(800)
        # normal：无需特殊处理

    def _pin_to_bottom(self) -> None:
        # 悬停或有弹出菜单/对话框时不下沉，避免「刚要点就被压到底层」而点不动
        if self._layer != "bottom" or self._hovering or self._popup_active():
            return
        _win_send_to_bottom(int(self.winId()))

    @staticmethod
    def _popup_active() -> bool:
        app = QApplication.instance()
        if app is None:
            return False
        return app.activePopupWidget() is not None or app.activeModalWidget() is not None

    def enterEvent(self, e):  # noqa: N802
        # 鼠标移到挂件上→升到可点层（仅置底模式）；离开后由定时器重新沉底
        if self._layer == "bottom":
            self._hovering = True
            self.raise_()
        super().enterEvent(e)

    def leaveEvent(self, e):  # noqa: N802
        if self._layer == "bottom":
            self._hovering = False
        super().leaveEvent(e)

    def changeEvent(self, e):  # noqa: N802
        # 窗口失去激活（用户切到别的应用）→ 通知 app，决定是否「召唤后自动沉回桌面」
        if e.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.window_deactivated.emit()
        super().changeEvent(e)

    @property
    def layer(self) -> str:
        return self._layer

    def set_on_top(self, on: bool) -> None:
        """兼容旧接口：True → 置顶；False → 置底。"""
        self.set_layer("top" if on else "bottom")

    def _px(self, v: float) -> int:
        return _pxf(self._scale, v)

    def paintEvent(self, event):  # noqa: N802
        # 轻量投影：几层圆角矩形叠加模拟柔和阴影（无高斯模糊，重绘开销约为原来的 1/4）
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        ml, mt, mr, mb = self._px(20), self._px(16), self._px(20), self._px(20)
        card = QRectF(ml, mt, self.width() - ml - mr, self.height() - mt - mb)
        radius = self._px(18)
        dy = self._px(6)
        step = max(1.0, self._px(1.4))
        p.setPen(Qt.NoPen)
        for i in range(9, 0, -1):
            grow = i * step
            rr = QRectF(card).adjusted(-grow, -grow + dy, grow, grow + dy)
            p.setBrush(QColor(0, 0, 0, max(3, int(16 - i * 1.4))))
            p.drawRoundedRect(rr, radius + grow, radius + grow)
        p.end()

    # ---------- 样式 ----------
    def _qss(self) -> str:
        s = self._scale
        return f"""
        #card {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {BG_TOP}, stop:1 {BG_BOT});
            border:1px solid {BORDER}; border-radius:{_pxf(s,18)}px;
        }}
        QLabel {{ color:{FG}; font-family:'Microsoft YaHei UI','Segoe UI'; }}
        #title {{ color:{MUTED}; font-size:{_pxf(s,13)}px; font-weight:600; }}
        #muted {{ color:{MUTED}; font-size:{_pxf(s,12)}px; }}
        #faint {{ color:{FAINT}; font-size:{_pxf(s,11)}px; }}
        #sep {{ background:{BORDER}; border:none; }}
        QPushButton#iconbtn {{ color:{MUTED}; background:transparent; border:none;
            font-size:{_pxf(s,13)}px; border-radius:{_pxf(s,12)}px; }}
        QPushButton#iconbtn:hover {{ color:{FG}; background:{CHIP_BG}; }}
        QPushButton#refresh {{ color:#c7cee1; background:transparent; border:1px solid {BORDER};
            border-radius:{_pxf(s,11)}px; padding:{_pxf(s,6)}px {_pxf(s,15)}px;
            font-size:{_pxf(s,12)}px; font-weight:600; }}
        QPushButton#refresh:hover {{ background:{CHIP_BG}; }}
        QPushButton#refresh:disabled {{ color:{FAINT}; }}
        QPushButton#compare {{ color:#c7cee1; background:transparent; border:1px solid {BORDER};
            border-radius:{_pxf(s,11)}px; padding:{_pxf(s,6)}px {_pxf(s,13)}px;
            font-size:{_pxf(s,12)}px; font-weight:600; }}
        QPushButton#compare:hover {{ background:{CHIP_BG}; color:{FG}; }}
        QPushButton#book {{ color:#0c1220;
            background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2});
            border:none; border-radius:{_pxf(s,11)}px; padding:{_pxf(s,6)}px {_pxf(s,20)}px;
            font-size:{_pxf(s,12)}px; font-weight:700; }}
        QPushButton#book:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
            stop:0 {ACCENT2}, stop:1 {ACCENT2}); }}
        """

    # ---------- 构建 ----------
    def _build(self) -> None:
        self._outer = QVBoxLayout(self)
        self.card = QFrame()
        self.card.setObjectName("card")
        self._outer.addWidget(self.card)

        self.v = QVBoxLayout(self.card)

        # 顶部
        top = QHBoxLayout()
        top.setSpacing(8)
        self.dot = QLabel()
        top.addWidget(self.dot)
        self.lbl_title = QLabel("北京 → 悉尼 · 往返")
        self.lbl_title.setObjectName("title")
        top.addWidget(self.lbl_title)
        top.addStretch(1)
        self.btn_settings = QPushButton("⚙")
        self.btn_hide = QPushButton("✕")
        for b in (self.btn_settings, self.btn_hide):
            b.setObjectName("iconbtn")
            b.setCursor(Qt.PointingHandCursor)
        top.addWidget(self.btn_settings)
        top.addWidget(self.btn_hide)
        self.v.addLayout(top)

        # 价格 + 涨跌 pill + 往返角标
        prow = QHBoxLayout()
        prow.setSpacing(10)
        self.lbl_price = QLabel()
        prow.addWidget(self.lbl_price)
        self.lbl_pill = QLabel("")
        self.lbl_pill.hide()
        prow.addWidget(self.lbl_pill)
        prow.addStretch(1)
        self.lbl_rt = QLabel("往返总价")
        prow.addWidget(self.lbl_rt)
        self.v.addLayout(prow)

        # 提示行（无结果 / 占位）
        self.lbl_note = QLabel("等待首次查询…")
        self.lbl_note.setObjectName("muted")
        self.lbl_note.setWordWrap(True)
        self.v.addWidget(self.lbl_note)

        # 去程 / 返程分区容器
        self.legs_host = QVBoxLayout()
        self.legs_host.setContentsMargins(0, 0, 0, 0)
        legs_wrap = QWidget()
        legs_wrap.setLayout(self.legs_host)
        self.v.addWidget(legs_wrap)

        # sparkline
        self.spark = Sparkline()
        self.v.addWidget(self.spark)

        # 趋势 / 日期
        self.lbl_trend = QLabel("")
        self.lbl_trend.setObjectName("faint")
        self.v.addWidget(self.lbl_trend)

        # 分隔线
        self.sep = QFrame()
        self.sep.setObjectName("sep")
        self.sep.setFixedHeight(1)
        self.v.addWidget(self.sep)

        # 底部
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.lbl_status = QLabel(self._status_text)
        self.lbl_status.setObjectName("faint")
        bottom.addWidget(self.lbl_status)
        bottom.addStretch(1)
        self.btn_compare = QPushButton("比价 ▾")
        self.btn_compare.setObjectName("compare")
        self.btn_compare.setToolTip("一键打开携程 / Trip.com / 去哪儿 / Skyscanner / 飞猪 / 同程 比价")
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.setObjectName("refresh")
        self.btn_book = QPushButton("订票 ▾")
        self.btn_book.setObjectName("book")
        self.btn_book.setToolTip("按当前最优航班的精确航线+日期，打开携程 / Trip.com 可下单结果页")
        for b in (self.btn_compare, self.btn_refresh, self.btn_book):
            b.setCursor(Qt.PointingHandCursor)
        bottom.addWidget(self.btn_compare)
        bottom.addWidget(self.btn_refresh)
        bottom.addWidget(self.btn_book)
        self.v.addLayout(bottom)

        self.btn_settings.clicked.connect(self.settings_requested)
        self.btn_hide.clicked.connect(self.hide_requested)
        self.btn_refresh.clicked.connect(self.refresh_requested)
        self.btn_book.clicked.connect(self._show_book_menu)
        self.btn_compare.clicked.connect(self._show_compare_menu)

    # ---------- 缩放/尺寸 ----------
    def _apply_metrics(self) -> None:
        s = self._scale
        self.setFixedWidth(_pxf(s, BASE_W))
        self.card.setStyleSheet(self._qss())
        self._outer.setContentsMargins(
            _pxf(s, 20), _pxf(s, 16), _pxf(s, 20), _pxf(s, 20)
        )
        self.v.setContentsMargins(_pxf(s, 18), _pxf(s, 15), _pxf(s, 18), _pxf(s, 15))
        self.v.setSpacing(_pxf(s, 8))
        self.dot.setFixedSize(_pxf(s, 9), _pxf(s, 9))
        for b in (self.btn_settings, self.btn_hide):
            b.setFixedSize(_pxf(s, 24), _pxf(s, 24))
        self.spark.setMinimumHeight(_pxf(s, 56))
        self.legs_host.setSpacing(_pxf(s, 8))
        self.lbl_rt.setStyleSheet(
            f"color:{FAINT};font-size:{_pxf(s,10)}px;"
            f"background:{CHIP_BG};border-radius:{_pxf(s,7)}px;"
            f"padding:{_pxf(s,2)}px {_pxf(s,8)}px;"
        )
        self.adjustSize()

    def _set_dot(self, color: str) -> None:
        self.dot.setStyleSheet(f"background:{color};border-radius:{self._px(4)}px;")

    def wheelEvent(self, e):  # noqa: N802
        step = 1.1 if e.angleDelta().y() > 0 else (1 / 1.1)
        new = min(SCALE_MAX, max(SCALE_MIN, self._scale * step))
        if abs(new - self._scale) < 1e-3:
            e.accept()
            return
        right = self.geometry().right()
        bottom = self.geometry().bottom()
        self._scale = new
        self._apply_metrics()
        self._repaint_all()
        # 以右下角为锚，缩放后不跑偏（贴边体验更好）
        self.adjustSize()
        g = self.geometry()
        self.move(right - g.width(), bottom - g.height())
        self.scale_changed.emit(self._scale)
        e.accept()

    # ---------- 小组件工厂 ----------
    def _chip(self, text: str, fg: str = MUTED, bg: str = CHIP_BG) -> QLabel:
        s = self._scale
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"background:{bg};color:{fg};border-radius:{_pxf(s,9)}px;"
            f"padding:{_pxf(s,3)}px {_pxf(s,9)}px;font-size:{_pxf(s,11)}px;"
        )
        return lbl

    def _badge(self, text: str, color: str) -> QLabel:
        s = self._scale
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"background:rgba(0,0,0,0);color:{color};border:1px solid {color};"
            f"border-radius:{_pxf(s,7)}px;padding:{_pxf(s,1)}px {_pxf(s,7)}px;"
            f"font-size:{_pxf(s,11)}px;font-weight:700;"
        )
        return lbl

    def _leg_widget(self, itin, kind: str, color: str) -> QWidget:
        """构建一程（去/返）明细块。"""
        s = self._scale
        box = QVBoxLayout()
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(_pxf(s, 3))

        # 第一行：徽标 + 日期 + 时长/中转
        h1 = QHBoxLayout()
        h1.setSpacing(_pxf(s, 7))
        h1.addWidget(self._badge(kind, color))
        d = QLabel(date_label(itin.dep_date) or itin.depart_date)
        d.setStyleSheet(f"color:{MUTED};font-size:{_pxf(s,12)}px;font-weight:600;")
        h1.addWidget(d)
        h1.addStretch(1)
        stops_c = GREEN if itin.stops == 0 else MUTED
        meta = QLabel(f"{itin.total_str()} · {itin.stops_str()}")
        meta.setStyleSheet(f"color:{stops_c};font-size:{_pxf(s,11)}px;")
        h1.addWidget(meta)
        box.addLayout(h1)

        # 第二行：机场中文名 + 时刻（含跨日 +N）
        off = itin.day_offset()
        plus = (
            f"<sup style='color:{GOLD};font-size:{_pxf(s,10)}px'>+{off}</sup>"
            if off > 0 else ""
        )
        an = _pxf(s, 15)
        tn = _pxf(s, 13)
        route = QLabel(
            f"<span style='color:{FG};font-size:{an}px;font-weight:700'>{itin.origin_cn()}</span>"
            f"<span style='color:{ACCENT2};font-size:{tn}px'> {itin.dep_time_str()}</span>"
            f"<span style='color:{FAINT};font-size:{tn}px'>　✈　</span>"
            f"<span style='color:{FG};font-size:{an}px;font-weight:700'>{itin.dest_cn()}</span>"
            f"<span style='color:{ACCENT2};font-size:{tn}px'> {itin.arr_time_str()}</span>{plus}"
        )
        route.setTextFormat(Qt.RichText)
        box.addWidget(route)

        # 第三行：中转城市（如有）
        if itin.layovers:
            lo = QLabel("经 " + "  ".join(lv.label_cn() for lv in itin.layovers))
            lo.setStyleSheet(f"color:{FAINT};font-size:{_pxf(s,11)}px;")
            lo.setWordWrap(True)
            box.addWidget(lo)

        wrap = QWidget()
        wrap.setLayout(box)
        return wrap

    def _sep_thin(self) -> QFrame:
        f = QFrame()
        f.setFixedHeight(1)
        f.setStyleSheet(f"background:{BORDER};border:none;")
        return f

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    # ---------- 对外接口 ----------
    def set_price_value(self, v) -> None:
        self._hist_price = v
        s = self._scale
        num = "----" if v is None else f"{int(v):,}"
        self.lbl_price.setText(
            f"<span style='font-size:{_pxf(s,18)}px;font-weight:600;color:{MUTED}'>¥</span> "
            f"<span style='font-size:{_pxf(s,33)}px;font-weight:800;color:{FG}'>{num}</span>"
        )

    def set_status(self, text: str) -> None:
        self._status_text = text
        self.lbl_status.setText(text)

    def set_offline(self, offline: bool, ts: float | None = None) -> None:
        """断网/抓取失败时：保留最近价格并给出明确提示（不清空、不误报「无航班」）。"""
        self._offline = offline
        if not offline:
            return
        if ts is not None:
            self._last_ts = ts
        self._set_dot(GOLD)
        if self._last_ts is not None:
            hhmm = time.strftime("%H:%M", time.localtime(self._last_ts))
            self._status_text = f"⚠ 离线/查询失败 · 显示 {hhmm} 价格 · 联网后自动更新"
        else:
            self._status_text = "⚠ 离线/查询失败 · 暂无历史价 · 联网后自动更新"
        self.lbl_status.setText(self._status_text)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.btn_refresh.setEnabled(not busy)
        self.btn_refresh.setText("查询中" if busy else "刷新")
        if busy:
            self._set_dot(ACCENT)

    def booking_url(self) -> str:
        return self._book_url

    def set_default_route(self, origin: str, dest: str, dep: str, ret: str) -> None:
        """在首次查询出结果前，也让「比价」可用（用配置里的锚点航线/日期）。"""
        if self._cmp_route is None:
            self._cmp_route = (origin, dest, dep, ret)

    def _menu_qss(self) -> str:
        s = self._scale
        return f"""
        QMenu {{ background:{BG_TOP}; border:1px solid {BORDER};
            border-radius:{_pxf(s,10)}px; padding:{_pxf(s,6)}px;
            color:{FG}; font-family:'Microsoft YaHei UI','Segoe UI';
            font-size:{_pxf(s,12)}px; }}
        QMenu::item {{ padding:{_pxf(s,7)}px {_pxf(s,14)}px;
            border-radius:{_pxf(s,7)}px; }}
        QMenu::item:selected {{ background:{CHIP_BG}; }}
        QMenu::item:disabled {{ color:{MUTED}; }}
        QMenu::separator {{ height:1px; background:{BORDER};
            margin:{_pxf(s,5)}px {_pxf(s,8)}px; }}
        """

    def _show_compare_menu(self) -> None:
        if not self._cmp_route:
            return
        o, d, dep, ret = self._cmp_route
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())
        hdr = menu.addAction(
            f"比价 · {airports.city_name(o, o)} → {airports.city_name(d, d)}   {dep} / {ret}"
        )
        hdr.setEnabled(False)
        menu.addSeparator()
        for it in compare.compare_links(o, d, dep, ret, self._gf_url):
            tail = "  →结果页" if it["direct"] else "  →机票页"
            act = QAction(f"{it['name']}    {it['note']}{tail}", menu)
            act.triggered.connect(
                lambda _checked=False, u=it["url"]: QDesktopServices.openUrl(QUrl(u))
            )
            menu.addAction(act)
        menu.exec(self.btn_compare.mapToGlobal(self.btn_compare.rect().bottomLeft()))

    def _show_book_menu(self) -> None:
        """订票菜单：先标注「这趟航班」，再给可直接下单的精确航线+日期入口。"""
        ob = self._book_ob
        if ob is None:
            # 尚无结果：退化为比价入口（若已有默认航线）
            if self._cmp_route:
                self._show_compare_menu()
            return
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_qss())
        # —— 标注当前监控的这趟航班，便于在结果页里一眼认出 ——
        h1 = menu.addAction(f"去订票 · {ob.airlines_str()}    ¥{ob.price:,}")
        h1.setEnabled(False)
        sub = f"本趟 {ob.route_str()}   {ob.depart_date} {ob.dep_time_str()}发 · {ob.stops_str()}"
        lay = ob.layover_cn_str()
        if lay:
            sub += f" · {lay}中转"
        h2 = menu.addAction(sub)
        h2.setEnabled(False)
        menu.addSeparator()
        for it in compare.book_links(
            ob.origin, ob.dest, ob.depart_date, ob.return_date, self._gf_url,
            getattr(ob, "book_url", "")
        ):
            act = QAction(f"{it['name']}    {it['note']}", menu)
            act.triggered.connect(
                lambda _checked=False, u=it["url"]: QDesktopServices.openUrl(QUrl(u))
            )
            menu.addAction(act)

        # —— 拆票入口（两段单程分开买，仅当更便宜时出现）——
        if self._split_data is not None:
            so, sr, sp = self._split_data
            menu.addSeparator()
            sh = menu.addAction(f"💡 拆票更省 ¥{sp:,}（两段单程·分别下单）")
            sh.setEnabled(False)
            for tag, leg, col in (("① 订去程单程", so, "去程"), ("② 订返程单程", sr, "返程")):
                sub = f"{tag} · {leg.route_str()} {leg.depart_date} · {leg.airlines_str()} ¥{leg.price:,}"
                a = QAction(sub, menu)
                u = getattr(leg, "url", "") or self._gf_url
                a.triggered.connect(
                    lambda _checked=False, url=u: QDesktopServices.openUrl(QUrl(url))
                )
                menu.addAction(a)

        menu.exec(self.btn_book.mapToGlobal(self.btn_book.rect().bottomLeft()))

    def load_history(self, spark_prices: list) -> None:
        """冷启动：仅用历史价渲染（无本次结果）。"""
        self._result = None
        self._spark_prices = list(spark_prices)
        self._spark_baseline = None
        self._hist_price = spark_prices[-1] if spark_prices else None
        self._repaint_all()

    def update_view(self, result: CheckResult, spark: list) -> None:
        self._result = result
        self._spark_prices = [pr for _, pr in spark]
        self._spark_baseline = getattr(result, "trend_baseline", None)
        self._repaint_all()

    # ---------- 渲染 ----------
    def _repaint_all(self) -> None:
        s = self._scale
        self.spark.set_data(self._spark_prices, self._spark_baseline)
        self._clear(self.legs_host)

        result = self._result
        ob = result.overall_best if result is not None else None

        if result is None:
            # 冷启动 / 历史态
            self.set_price_value(self._hist_price)
            self.lbl_pill.hide()
            self.lbl_note.show()
            self.lbl_note.setText(
                "已载入历史价格，等待刷新以获取双程明细…"
                if self._hist_price is not None else "等待首次查询…"
            )
            self.lbl_trend.setText("")
            self._set_dot(ACCENT if self._busy else GREEN)
            self.lbl_status.setText(self._status_text)
            return

        if ob is None:
            self.set_price_value(None)
            self.lbl_pill.hide()
            self.lbl_note.show()
            self.lbl_note.setText("本次未找到符合条件的航班，可在设置放宽中转/时长限制。")
            self.lbl_trend.setText("")
            self._set_dot(FAINT)
            self._footer(result)
            return

        self._gf_url = ob.url
        self._book_ob = ob
        self._book_url = compare.primary_book_url(
            ob.origin, ob.dest, ob.depart_date, ob.return_date, ob.url, getattr(ob, "book_url", "")
        )
        self._cmp_route = (ob.origin, ob.dest, ob.depart_date, ob.return_date)
        self.set_price_value(ob.price)
        self.lbl_title.setText(f"{ob.route_label} · 往返")
        self.lbl_note.hide()

        # 涨跌 pill
        prices = self._spark_prices
        if len(prices) >= 2 and prices[-2]:
            diff = (prices[-1] - prices[-2]) / prices[-2] * 100
            if abs(diff) >= 0.1:
                down = diff < 0
                c = GREEN if down else RED
                rgba = "rgba(47,224,166,30)" if down else "rgba(255,107,129,30)"
                self.lbl_pill.setText(f"{'▼' if down else '▲'} {abs(diff):.1f}%")
                self.lbl_pill.setStyleSheet(
                    f"background:{rgba};color:{c};border-radius:{_pxf(s,10)}px;"
                    f"padding:{_pxf(s,3)}px {_pxf(s,10)}px;"
                    f"font-size:{_pxf(s,12)}px;font-weight:700;"
                )
                self.lbl_pill.show()
            else:
                self.lbl_pill.hide()
        else:
            self.lbl_pill.hide()

        # 去程
        self.legs_host.addWidget(self._leg_widget(ob, "去程", ACCENT))
        self.legs_host.addWidget(self._sep_thin())
        # 返程
        rl = getattr(result, "return_leg", None)
        if rl is not None:
            rw = self._leg_widget(rl, "返程", RETURN_C)
            rw.setToolTip("返程为该日期「中转少/耗时短」的代表行程；\n价格以往返总价为准，实际与往返票搭配可能略有差异")
            self.legs_host.addWidget(rw)
        else:
            ph = QLabel(
                f"返程（{date_label(_date_tuple(ob.return_date))}）明细将在刷新后显示"
            )
            ph.setObjectName("faint")
            ph.setWordWrap(True)
            self.legs_host.addWidget(ph)

        # 风险（合并去/返，去重，最多 3 条）
        risks = list(ob.risks or [])
        if rl is not None:
            risks += [f"返程·{r}" for r in (rl.risks or [])]
        if risks:
            rc = QHBoxLayout()
            rc.setSpacing(_pxf(s, 6))
            seen = []
            for r in risks:
                if r in seen:
                    continue
                seen.append(r)
                rc.addWidget(self._chip("⚠ " + r, fg=GOLD, bg="rgba(255,206,92,26)"))
                if len(seen) >= 3:
                    break
            rc.addStretch(1)
            rw = QWidget()
            rw.setLayout(rc)
            self.legs_host.addWidget(rw)

        # 拆票更省（仅当去/返两段单程合计明显更便宜时才提示）
        self._split_data = None
        if getattr(result, "split_cheaper", False):
            so, sr, sp = result.split_out, result.split_ret, result.split_price
            self._split_data = (so, sr, sp)
            save = ob.price - sp
            self.legs_host.addWidget(self._sep_thin())
            hdr = QLabel(f"💡 拆票更省 ¥{sp:,} · 省¥{save:,}（两段单程·分别下单）")
            hdr.setStyleSheet(
                f"color:{GREEN};font-size:{_pxf(s,13)}px;font-weight:800;"
            )
            hdr.setWordWrap(True)
            self.legs_host.addWidget(hdr)
            self.legs_host.addWidget(self._leg_widget(so, "单程去", ACCENT))
            self.legs_host.addWidget(self._leg_widget(sr, "单程返", RETURN_C))
            tip = QLabel("两张独立机票·需分别下单；退改/行李各自独立，前段延误不保后段（点「订票」查看拆票入口）")
            tip.setObjectName("faint")
            tip.setWordWrap(True)
            self.legs_host.addWidget(tip)

        # 趋势
        if result.trend_baseline:
            src = getattr(result, "trend_source", None)
            lab = "Travelpayouts" if src == "travelpayouts" else "自建历史"
            self.lbl_trend.setText(f"趋势基准 ¥{result.trend_baseline:,} · {lab}")
        else:
            self.lbl_trend.setText("趋势基准积累中 · 无需 token")

        self._set_dot(GOLD if result.alerted else GREEN)
        self._footer(result)

    def _footer(self, result: CheckResult) -> None:
        self._last_ts = result.timestamp
        hhmm = time.strftime("%H:%M", time.localtime(result.timestamp))
        tag = " · 已提醒 🔔" if result.alerted else ""
        self._status_text = f"更新 {hhmm} · {self._rel_time(result.timestamp)}{tag}"
        self.lbl_status.setText(self._status_text)

    @staticmethod
    def _rel_time(ts: float) -> str:
        secs = max(0.0, time.time() - ts)
        if secs < 60:
            return "刚刚"
        m = int(secs // 60)
        if m < 60:
            return f"{m}分钟前"
        h = int(m // 60)
        return f"{h}小时前"

    def tick_relative(self) -> None:
        """每分钟由主程序调用一次，只刷新「X分钟前」文案，不重新抓取。"""
        if self._busy or self._last_ts is None:
            return
        hhmm = time.strftime("%H:%M", time.localtime(self._last_ts))
        if self._offline:
            self._status_text = f"⚠ 离线 · 上次成功 {hhmm}（{self._rel_time(self._last_ts)}）· 联网后自动更新"
            self.lbl_status.setText(self._status_text)
            return
        if self._result is None:
            return
        tag = " · 已提醒 🔔" if getattr(self._result, "alerted", False) else ""
        self._status_text = f"更新 {hhmm} · {self._rel_time(self._last_ts)}{tag}"
        self.lbl_status.setText(self._status_text)

    # ---------- 拖动 ----------
    def mousePressEvent(self, e):  # noqa: N802
        if e.button() == Qt.LeftButton:
            self._drag_off = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):  # noqa: N802
        if self._drag_off is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_off)
            e.accept()

    def mouseReleaseEvent(self, e):  # noqa: N802
        self._drag_off = None


def _date_tuple(date_str: str):
    """'2026-10-05' -> (2026,10,5)；失败返回 None。"""
    try:
        y, m, d = date_str.split("-")
        return (int(y), int(m), int(d))
    except (ValueError, AttributeError):
        return None
