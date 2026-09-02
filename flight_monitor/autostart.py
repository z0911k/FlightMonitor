"""开机自启（写入 HKCU Run 注册表项，使用 pythonw 静默启动）。"""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

from .config import BASE_DIR

APP_NAME = "FlightMonitor"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _command() -> str:
    # 打包成 exe（frozen）后，自启命令就是 exe 本身；源码运行时用 pythonw + 入口脚本。
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable).resolve()}"'
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    launcher = pyw if pyw.exists() else exe
    entry = BASE_DIR / "run_flight_monitor.pyw"
    return f'"{launcher}" "{entry}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            val, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(val)
    except FileNotFoundError:
        return False
    except OSError:
        return False


def enable() -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())


def disable() -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, APP_NAME)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def apply(flag: bool) -> None:
    if flag:
        enable()
    else:
        disable()
