"""The icon next to the clock, and "Start with Windows".

Needs pystray + Pillow. Without them the app simply has no tray icon and
closing the window quits, like before.
"""
import sys
import threading
from pathlib import Path

try:
    import winreg
except ImportError:  # not on Windows
    winreg = None

try:
    import pystray
    from PIL import Image
except ImportError:
    pystray = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Vinyl Presence"


def available():
    return pystray is not None and sys.platform == "win32"


class Tray:
    """Callbacks run on pystray's thread; the caller must hand them to the Tk thread."""

    def __init__(self, icon_path, now_text, is_playing, on_open, on_stop, on_quit):
        image = Image.open(icon_path).resize((64, 64), Image.LANCZOS)
        menu = pystray.Menu(
            pystray.MenuItem("Open Vinyl Presence", lambda: on_open(), default=True),
            pystray.MenuItem(lambda item: now_text(), None, enabled=False),
            pystray.MenuItem("Stop", lambda: on_stop(), enabled=lambda item: is_playing()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda: on_quit()),
        )
        self.icon = pystray.Icon("vinyl-presence", image, "Vinyl Presence", menu)
        threading.Thread(target=self.icon.run, daemon=True, name="tray").start()

    def update(self, tooltip):
        self.icon.title = tooltip[:127]
        self.icon.update_menu()

    def notify(self, message, title="Vinyl Presence"):
        try:
            self.icon.notify(message, title)
        except Exception:  # notifications are a nice-to-have
            pass

    def stop(self):
        self.icon.stop()


# ---------------------------------------------------------------- start with Windows

def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --tray'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    script = Path(__file__).with_name("app.py")
    return f'"{pythonw if pythonw.exists() else sys.executable}" "{script}" --tray'


def _read_startup():
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except OSError:
        return None


def startup_enabled():
    return _read_startup() is not None


def set_startup(enabled):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


def refresh_startup():
    """If the app moved (or was updated to a new place), point the startup entry at it again."""
    current = _read_startup()
    if current is not None and current != startup_command():
        set_startup(True)
