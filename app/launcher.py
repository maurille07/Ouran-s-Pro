"""Start Ouranòs-Pro as a desktop app: its own window, no console, no address bar.

Run with ``pythonw -m app.launcher`` (the desktop shortcut does exactly that).

What it does:
1. starts the local server in the background (unless it is already running);
2. opens the operator screen in an Edge/Chrome *app window* -- a plain window with
   the Ouranòs-Pro icon and title, without tabs or address bar;
3. waits until that window is closed, then stops the server and exits.

Options:  --projector   also open the projector screen full-screen on the 2nd monitor
          --port 9000   use another port
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

# pythonw has no console: give libraries that print (progress bars...) somewhere to write.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")   # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")   # noqa: SIM115

from . import config  # noqa: E402

log = logging.getLogger("ouranos.launcher")


def message_box(text: str, error: bool = False) -> None:
    """Show a message without a console (Windows); print it elsewhere."""
    if sys.platform == "win32":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, text, config.APP_NAME, 0x10 if error else 0x40)
    else:
        print(text)


def find_browser() -> str | None:
    """Edge or Chrome: both can open a web page as a stand-alone app window."""
    if sys.platform == "win32":
        roots = [os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.environ.get("LOCALAPPDATA", "")]
        for relative in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            for root in roots:
                if root and (Path(root) / relative).is_file():
                    return str(Path(root) / relative)
        return None
    if sys.platform == "darwin":
        for path in ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                     "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"):
            if Path(path).is_file():
                return path
        return None
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
        found = shutil.which(name)
        if found:
            return found
    return None


def profile_dir(name: str) -> Path:
    """A private browser profile, so our window is a separate process we can wait for."""
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share")
    return base / "OuranosPro" / name


def is_running(base_url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base_url}/api/state", timeout=1):
            return True
    except Exception:
        return False


def second_monitor_position() -> tuple[int, int] | None:
    """Top-left corner of a non-primary monitor (Windows), or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    found: list[tuple[int, int]] = []

    def callback(monitor, _dc, _rect, _data):  # type: ignore[no-untyped-def]
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)) and not info.dwFlags & 1:
            found.append((info.rcMonitor.left, info.rcMonitor.top))
        return 1

    proc = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                              ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)(callback)
    ctypes.windll.user32.EnumDisplayMonitors(None, None, proc, 0)
    return found[0] if found else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Ouranòs-Pro in its own window.")
    parser.add_argument("--projector", action="store_true", help="also open the projector screen full-screen")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    args = parser.parse_args()
    config.setup_logging()
    base_url = f"http://127.0.0.1:{args.port}"

    if not config.BIBLE_DB.exists():
        message_box("Ouranòs-Pro is not installed yet.\n\nPlease double-click Setup.bat first.", error=True)
        return 1

    # 1. Server (in this process, on a background thread) unless one is already running.
    server = None
    if not is_running(base_url):
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(
            "app.main:app", host=config.DEFAULT_HOST, port=args.port, log_config=None, log_level="warning",
        ))
        threading.Thread(target=server.run, name="server", daemon=True).start()
        deadline = time.time() + 60
        while time.time() < deadline and not is_running(base_url):
            time.sleep(0.2)
        if not is_running(base_url):
            log.error("The server did not start")
            message_box("Ouranòs-Pro could not start.\n\nDetails are in the file logs\\ouranos.log.", error=True)
            return 1

    # 2. Windows.
    browser = find_browser()
    if browser is None:
        import webbrowser

        webbrowser.open(f"{base_url}/operator")
        message_box("Ouranòs-Pro is open in your web browser.\n\nClick OK here when you have finished, to stop it.")
        if server is not None:
            server.should_exit = True
        return 0

    common = ["--no-first-run", "--no-default-browser-check", "--disable-session-crashed-bubble"]
    if args.projector:
        position = second_monitor_position() or (0, 0)
        subprocess.Popen([browser, f"--kiosk", f"{base_url}/display", f"--user-data-dir={profile_dir('projector')}",
                          f"--window-position={position[0]},{position[1]}", *common])
    window = subprocess.Popen([browser, f"--app={base_url}/operator", f"--user-data-dir={profile_dir('app')}",
                               "--window-size=1440,900", *common])
    log.info("App window opened (%s)", Path(browser).name)

    # 3. Closing the window closes the app.
    window.wait()
    log.info("App window closed")
    if server is not None:
        server.should_exit = True
        time.sleep(1.5)                 # let the server stop the microphone cleanly
    return 0


if __name__ == "__main__":
    code = main()
    logging.shutdown()
    # A model may still be loading on a background thread; the window is closed, so
    # leave now instead of keeping an invisible process alive until it finishes.
    os._exit(code)
