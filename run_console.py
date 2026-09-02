"""带控制台的调试入口：能看到完整报错堆栈。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flight_monitor.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
