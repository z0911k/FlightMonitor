"""应用主程序：悬浮窗 + 托盘 + 后台查询线程 + 定时刷新。"""
from __future__ import annotations

import copy
import sys
import time
import webbrowser

from PySide6.QtCore import QSharedMemory, QThread, QTimer, Signal
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import autostart, compare, monitor, storage
from .config import load_config, save_config
from .notify import notify
from .storage import OVERALL
from .ui_settings import SettingsDialog
from .ui_widget import FloatingWidget, make_app_icon


class CheckThread(QThread):
    """在后台线程执行一次检查，避免阻塞界面。"""

    progress = Signal(int, int, str)
    result_ready = Signal(object)
    split_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self._cfg = cfg

    def run(self) -> None:  # noqa: D401
        try:
            # 先只跑往返扫描，尽快把头条价格推给界面（拆票另行异步补齐）
            res = monitor.run_check(
                self._cfg,
                lambda d, t, m: self.progress.emit(d, t, m),
                do_split=False,
            )
            self.result_ready.emit(res)
            # 往返有结果才值得再花时间扫拆票；失败/离线则跳过
            if getattr(res, "overall_best", None) is not None:
                try:
                    self.split_ready.emit(monitor.scan_split(self._cfg))
                except Exception:  # noqa: BLE001 — 拆票是增强项，失败不影响主流程
                    pass
        except Exception as e:  # noqa: BLE001
            self.failed.emit(f"{type(e).__name__}: {e}")


class FlightMonitorApp:
    def __init__(self):
        self.app = QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)

        self.cfg = load_config()
        storage.init_db()

        self.icon = make_app_icon()
        self.widget = FloatingWidget(scale=float(self.cfg.get("widget_scale", 1.0)))
        self.widget.setWindowIcon(self.icon)
        self._thread: CheckThread | None = None

        # 让「比价」在首次查询前也可用：用主航线锚点做默认
        routes = self.cfg.get("routes", [])
        primary = next((r for r in routes if r.get("primary")), routes[0] if routes else None)
        if primary:
            self.widget.set_default_route(
                primary["origin"], primary["dest"],
                self.cfg.get("depart_start") or self.cfg.get("anchor_depart", ""),
                self.cfg.get("return_start") or self.cfg.get("anchor_return", ""),
            )

        # 缩放记忆（防抖写盘）
        self._pending_scale: float | None = None
        self._layer_actions: dict = {}
        self._prev_layer: str = "bottom"  # 「前置↔沉回桌面」切换时记住桌面层级
        self._summoned: bool = False      # 是否为「临时召唤到前面」（非用户显式置顶）
        self._summon_ts: float = 0.0      # 召唤时刻，用于过渡态去抖
        self._last_good_result = None     # 最近一次「成功拿到价格」的结果（断网时回退展示）
        self._scale_timer = QTimer()
        self._scale_timer.setSingleShot(True)
        self._scale_timer.timeout.connect(self._save_scale)

        self._build_tray()
        self._connect()

        self._timer = QTimer()
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self.start_check)

        # 每分钟只刷新「X分钟前」文案（不抓取），让「是否在工作」一目了然
        self._rel_timer = QTimer()
        self._rel_timer.setSingleShot(False)
        self._rel_timer.timeout.connect(self.widget.tick_relative)
        self._rel_timer.start(60 * 1000)

        self.widget.show()
        self._place_widget()
        # 应用窗口层级（默认「置底」：像桌面挂件，不遮挡其它应用）
        self.widget.set_layer(self._initial_layer())
        self._place_widget()

        # 载入已有历史，先渲染一次
        self._render_from_history()

        # 启动后稍作延迟，自动跑第一次
        QTimer.singleShot(1000, self.start_check)
        self._reschedule()

        # 启动时把小窗召唤到前面一次：置底模式下它一上来就沉在桌面底层、会被 Chrome 等
        # 窗口完全挡住，容易让人以为「打不开」。这里主动前置一次让用户看到；用户点到别的
        # 窗口后会自动沉回桌面层（保持「不遮挡其它应用」的偏好），也可单击托盘图标再召唤。
        QTimer.singleShot(1400, self._present_on_start)

    def _present_on_start(self) -> None:
        """启动时把小窗召唤到最前面一次（不改写用户保存的层级偏好）。"""
        try:
            self._prev_layer = self.cfg.get("window_layer", "bottom")
            self._summoned = True
            self._summon_ts = time.monotonic()
            self.widget.show()
            self.widget.set_layer("top")   # 仅临时置顶展示，不落盘（不动 cfg["window_layer"]）
            self._place_widget_near_cursor()
            self.widget.raise_()
            self.widget.activateWindow()
            self.widget.set_status("已召唤到前面 · 点别处会自动沉回桌面（单击托盘图标可再召唤）")
        except Exception:  # noqa: BLE001
            pass

    # ---------- 初始化 ----------
    def _initial_layer(self) -> str:
        """启动层级：优先 window_layer；缺失时由旧字段 always_on_top 推断。"""
        mode = self.cfg.get("window_layer")
        if mode not in ("bottom", "normal", "top"):
            mode = "top" if bool(self.cfg.get("always_on_top", False)) else "bottom"
        self.cfg["window_layer"] = mode
        return mode

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("机票价格监控")
        menu = QMenu()
        menu.addAction("召唤到前面 / 沉回桌面", self._toggle_front)
        menu.addAction("移到当前屏幕并前置", self._show_widget)
        menu.addAction("隐藏", self._hide_widget)

        layer_menu = menu.addMenu("窗口层级")
        group = QActionGroup(layer_menu)
        group.setExclusive(True)
        cur = self.cfg.get("window_layer", "bottom")
        for mode, label in (
            ("bottom", "置底（桌面挂件·不遮挡其它窗口）"),
            ("normal", "普通"),
            ("top", "置顶（常驻可见）"),
        ):
            act = layer_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(mode == cur)
            act.triggered.connect(lambda _=False, m=mode: self._set_layer(m))
            group.addAction(act)
            self._layer_actions[mode] = act

        menu.addSeparator()
        menu.addAction("立即刷新", self.start_check)
        menu.addAction("设置…", self.open_settings)
        menu.addSeparator()
        menu.addAction("退出", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _set_layer(self, mode: str, *, summoned: bool = False, move: bool = True) -> None:
        self.widget.show()
        self.widget.set_layer(mode)
        if move:
            self._place_widget_near_cursor()
        self._summoned = bool(summoned)
        self.cfg["window_layer"] = mode
        self.cfg["always_on_top"] = (mode == "top")  # 兼容旧字段
        try:
            save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass
        act = self._layer_actions.get(mode)
        if act is not None:
            act.setChecked(True)
        label = {"bottom": "已置底（不遮挡其它窗口）", "normal": "已设为普通层级",
                 "top": "已钉在最上层"}.get(mode, "")
        self.widget.set_status(label)

    def _connect(self) -> None:
        self.widget.refresh_requested.connect(self.start_check)
        self.widget.settings_requested.connect(self.open_settings)
        self.widget.book_requested.connect(self.open_booking)
        self.widget.hide_requested.connect(self._hide_widget)
        self.widget.scale_changed.connect(self._on_scale_changed)
        self.widget.window_deactivated.connect(self._on_widget_deactivated)

    def _on_scale_changed(self, scale: float) -> None:
        self._pending_scale = float(scale)
        self._scale_timer.start(600)

    def _save_scale(self) -> None:
        if self._pending_scale is None:
            return
        self.cfg["widget_scale"] = round(self._pending_scale, 3)
        self._pending_scale = None
        try:
            save_config(self.cfg)
        except Exception:  # noqa: BLE001
            pass

    def _place_widget(self) -> None:
        screen = self.app.primaryScreen().availableGeometry()
        self.widget.adjustSize()
        x = screen.right() - self.widget.width() - 24
        y = screen.bottom() - self.widget.height() - 40
        self.widget.move(max(x, screen.left()), max(y, screen.top()))

    # ---------- 检查流程 ----------
    def start_check(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self.widget.set_busy(True)
        self.widget.set_status("查询中…")
        self._thread = CheckThread(copy.deepcopy(self.cfg))
        self._thread.progress.connect(self._on_progress)
        self._thread.result_ready.connect(self._on_result)
        self._thread.split_ready.connect(self._on_split)
        self._thread.failed.connect(self._on_failed)
        self._thread.start()

    def _on_progress(self, done: int, total: int, msg: str) -> None:
        self.widget.set_status(f"查询中 {done}/{total} · {msg}")

    def _on_result(self, res) -> None:
        self.widget.set_busy(False)
        ob = res.overall_best

        # 断网/被限流：本次一条价格都没抓到，且各航线均报错——不清空、不误报「无航班」，
        # 而是保留最近一次成功价格并明确提示离线，联网后下个周期自动恢复。
        if ob is None and any(getattr(r, "error", None) for r in res.routes):
            self._handle_fetch_failure(res)
            return

        self._last_good_result = res
        self.widget.set_offline(False)
        spark = storage.series_since(OVERALL, int(self.cfg.get("chart_days", 60)))
        self.widget.update_view(res, spark)

        if ob is not None:
            self.tray.setToolTip(f"机票监控 · 最低 ¥{ob.price:,}（{ob.route_label}）")

        if res.alerted and ob is not None:
            reasons = "；".join(res.alert_reasons)
            title = f"✈ 好价提醒 ¥{ob.price:,}"
            msg = (
                f"{ob.route_label}｜{ob.route_str()} · "
                f"{'直达' if ob.stops == 0 else str(ob.stops)+'中转'} · 全程{ob.total_str()}\n"
                f"{reasons}"
            )
            book_url = compare.primary_book_url(
                ob.origin, ob.dest, ob.depart_date, ob.return_date, ob.url,
                getattr(ob, "book_url", "")
            )
            notify(title, msg, url=book_url, sound=bool(self.cfg.get("sound", True)))
            self.tray.showMessage(title, msg, self.icon, 12000)

        self._reschedule()

    def _on_split(self, split) -> None:
        """拆票扫描完成（异步）：把结果并入最近一次成功结果并只重绘拆票区块，不触发提醒。"""
        res = self._last_good_result
        if res is None or split is None:
            return
        try:
            res.split_price, res.split_out, res.split_ret, res.split_label = split
        except Exception:  # noqa: BLE001
            return
        if not getattr(res, "split_cheaper", False):
            return  # 拆票不比往返便宜，无需更新界面
        spark = storage.series_since(OVERALL, int(self.cfg.get("chart_days", 60)))
        self.widget.update_view(res, spark)
        """抓取失败（多为断网/限流）：展示最近成功价格 + 离线提示，并加快重试。"""
        n_err = sum(1 for r in getattr(res, "routes", []) if getattr(r, "error", None)) if res else 0
        if self._last_good_result is not None:
            spark = storage.series_since(OVERALL, int(self.cfg.get("chart_days", 60)))
            self.widget.update_view(self._last_good_result, spark)
            self.widget.set_offline(True, ts=self._last_good_result.timestamp)
            self.tray.setToolTip("机票监控 · 离线，显示最近价格（联网后自动更新）")
        else:
            # 冷启动即离线：尽力用历史价渲染
            self._render_from_history()
            self.widget.set_offline(True)
            self.tray.setToolTip("机票监控 · 离线，等待联网后查询")
        self._reschedule(fast=True)

    def _on_failed(self, err: str) -> None:
        self.widget.set_busy(False)
        # run_check 整体异常（少见）：同样按离线降级，保留最近价格而非空白
        self._handle_fetch_failure()

    def _render_from_history(self) -> None:
        spark = storage.series_since(OVERALL, int(self.cfg.get("chart_days", 60)))
        if spark:
            self.widget.load_history([p for _, p in spark])
            self.widget.set_status("已载入历史，等待刷新…")

    # ---------- 交互 ----------
    def _on_tray_activated(self, reason) -> None:
        # 单击/双击托盘图标：在「前置·可点击」与「沉回桌面」之间切换。
        # 托盘图标永远不会被其它窗口盖住，所以这是被 Chrome 等挡住时最可靠的召唤方式。
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_front()

    def _toggle_front(self) -> None:
        if self.widget.layer == "top":
            # 沉回用户原来的桌面层级（默认置底），原地下沉不挪窝
            self._set_layer(self._prev_layer or "bottom", summoned=False, move=False)
            self.widget.set_status("已沉回桌面（再点托盘图标可召唤到前面）")
        else:
            self._prev_layer = self.widget.layer
            self._bring_front()

    def _bring_front(self) -> None:
        """把小窗召唤到最上层、移到当前屏幕、抢焦点，确保能被点击。"""
        self._set_layer("top", summoned=True, move=True)  # 置顶+移到当前屏幕+标记为「临时召唤」
        self._summon_ts = time.monotonic()
        self.widget.raise_()
        self.widget.activateWindow()
        self.widget.set_status("已前置 · 可点刷新/比价/设置（点别处会自动沉回桌面）")

    def _on_widget_deactivated(self) -> None:
        # 「临时召唤」到前面的小窗，一旦用户点了别的窗口就自动沉回桌面，避免久留遮挡 App。
        if not self._summoned or self.widget.layer != "top":
            return
        if time.monotonic() - self._summon_ts < 0.6:
            return  # 召唤瞬间的激活过渡，忽略
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return  # 比价下拉 / 设置对话框打开时不沉，否则会误沉
        self._set_layer(self._prev_layer or "bottom", summoned=False, move=False)
        self.widget.set_status("已沉回桌面（点托盘图标可再召唤）")

    def _hide_widget(self) -> None:
        # 「✕」隐藏不是关闭程序：小窗仍在后台监控。隐藏后弹一次托盘气泡，
        # 告诉用户「单击托盘飞机图标即可重新召唤」，避免误以为插件被关掉。
        self.widget.hide()
        try:
            self.tray.showMessage(
                "机票监控仍在后台运行",
                "小窗已隐藏（程序没关，仍在监控降价）。单击任务栏右下角的托盘飞机图标即可重新召唤。",
                self.icon, 6000,
            )
        except Exception:  # noqa: BLE001
            pass
        self.tray.setToolTip("机票监控 · 小窗已隐藏（单击图标召唤）")

    def _show_widget(self) -> None:
        # 菜单「显示·移到当前屏幕」：一律召唤到前面，避免被其它窗口盖住后点不到。
        if self.widget.layer != "top":
            self._prev_layer = self.widget.layer
        self.widget.show()
        self._bring_front()

    def _place_widget_near_cursor(self) -> None:
        from PySide6.QtGui import QCursor
        screen = self.app.screenAt(QCursor.pos()) or self.app.primaryScreen()
        geo = screen.availableGeometry()
        self.widget.adjustSize()
        x = geo.right() - self.widget.width() - 24
        y = geo.bottom() - self.widget.height() - 40
        self.widget.move(max(x, geo.left()), max(y, geo.top()))

    def open_booking(self) -> None:
        url = self.widget.booking_url()
        if url:
            webbrowser.open(url)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self.widget)
        if dlg.exec():
            dlg.apply_to(self.cfg)
            save_config(self.cfg)
            try:
                autostart.apply(bool(self.cfg.get("autostart")))
            except Exception:  # noqa: BLE001
                pass
            self._reschedule()
            # 用新的曲线跨度立即重绘（不必等下一次抓取）
            if self._last_good_result is not None:
                spark = storage.series_since(OVERALL, int(self.cfg.get("chart_days", 60)))
                self.widget.update_view(self._last_good_result, spark)
            else:
                self._render_from_history()
            self.widget.set_status("设置已保存")

    def _reschedule(self, fast: bool = False) -> None:
        if fast:
            self._timer.start(3 * 60 * 1000)  # 离线时 3 分钟后重试，联网后尽快恢复
            return
        mins = self.cfg.get("refresh_minutes")
        if mins is None:
            mins = int(self.cfg.get("refresh_hours", 3)) * 60
        mins = max(10, int(mins))  # 下限 10 分钟，避免过于频繁被风控
        self._timer.start(mins * 60 * 1000)

    def quit(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(2000)
        self.tray.hide()
        self.app.quit()

    def run(self) -> int:
        return self.app.exec()


_SINGLETON_KEY = "FlightMonitorSingleton_v1"
_guard: QSharedMemory | None = None


def _acquire_single_instance() -> bool:
    """返回 True 表示成功占用；False 表示已有实例在运行。"""
    global _guard
    _guard = QSharedMemory(_SINGLETON_KEY)
    if _guard.create(1):
        return True
    if _guard.error() == QSharedMemory.AlreadyExists:
        return False
    # 其它错误（极少见）：放行，避免误杀
    return True


def main() -> int:
    if not _acquire_single_instance():
        return 0
    return FlightMonitorApp().run()


if __name__ == "__main__":
    sys.exit(main())
