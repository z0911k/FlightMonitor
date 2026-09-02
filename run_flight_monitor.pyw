"""桌面机票价格监控 — 图形界面入口（双击运行，无控制台窗口）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flight_monitor.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
