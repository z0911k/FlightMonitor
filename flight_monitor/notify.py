"""Windows 桌面通知（toast）+ 提示音。"""
from __future__ import annotations

from typing import Optional

APP_ID = "机票价格监控"


def notify(title: str, message: str, url: Optional[str] = None, sound: bool = True,
           icon: Optional[str] = None, button_label: str = "去订票") -> None:
    """弹出 Windows toast；可带一个打开链接的按钮，并播放提示音。"""
    try:
        from winotify import Notification, audio

        toast = Notification(app_id=APP_ID, title=title, msg=message,
                             icon=icon or "")
        if url:
            toast.add_actions(label=button_label, launch=url)
        if sound:
            try:
                toast.set_audio(audio.Default, loop=False)
            except Exception:  # noqa: BLE001
                pass
        toast.show()
    except Exception:  # noqa: BLE001
        # toast 失败也不影响主流程
        pass

    if sound:
        _beep()


def _beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
    except Exception:  # noqa: BLE001
        pass
