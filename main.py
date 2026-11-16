"""Framio Connect launcher.

  python main.py                 desktop window (WebView2)
  python main.py --serve-only    just serve the UI and print the URL (development / browser testing)
  python main.py --autostart     start minimised (used by the Windows startup entry)
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import tempfile
import threading

from framio import APP_NAME
from framio.controller import Controller
from framio.webui import WebServer
from framio.winapi import set_dark_titlebar, sysmetric, user32

TITLE = APP_NAME
OV_TITLE = "Framio Drop"
OV_W = 340
OFFSCREEN = -6000

user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]


def tune_process():
    """Smooth, low-latency input handling."""
    sys.setswitchinterval(0.001)                 # hand the GIL over quickly (hook callbacks!)
    try:
        ctypes.windll.winmm.timeBeginPeriod(1)   # 1 ms timer resolution
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x8000)
    except Exception:
        pass


def _hwnd(title):
    return ctypes.windll.user32.FindWindowW(None, title)


def run_webview(ctrl, url, minimized):
    import webview
    win = webview.create_window(TITLE, url, width=1220, height=820, min_size=(1000, 700),
                                background_color="#0A1020", minimized=minimized)
    ov = webview.create_window(OV_TITLE, url + "overlay", width=OV_W, height=600, x=OFFSCREEN, y=OFFSCREEN,
                               frameless=True, on_top=True, focus=False, easy_drag=False,
                               background_color="#070B16")

    def titlebar(dark):
        h = _hwnd(TITLE)
        if h:
            set_dark_titlebar(h, dark)

    def attention():
        try:
            win.restore()
            h = _hwnd(TITLE)
            if h:
                ctypes.windll.user32.SetForegroundWindow(h)
        except Exception:
            pass

    def overlay_style():
        """Tool window, never takes focus (so it cannot disturb the drag), always on top."""
        h = _hwnd(OV_TITLE)
        if h:
            st = user32.GetWindowLongPtrW(h, -20)
            user32.SetWindowLongPtrW(h, -20, st | 0x80 | 0x08000000 | 0x8)
            user32.SetWindowPos(h, -1, OFFSCREEN, OFFSCREEN, OV_W, 600, 0x10 | 0x20 | 0x40)

    def overlay(action, edge=None):
        h = _hwnd(OV_TITLE)
        if not h:
            return
        if action == "show":
            x0, y0 = sysmetric(76), sysmetric(77)
            vw, vh = sysmetric(78), sysmetric(79)
            x = x0 + vw - OV_W if edge == "right" else x0
            user32.SetWindowPos(h, -1, x, y0, OV_W, vh, 0x10 | 0x40)       # NOACTIVATE | SHOWWINDOW
        else:
            user32.SetWindowPos(h, -1, OFFSCREEN, OFFSCREEN, OV_W, 600, 0x10)

    ctrl.titlebar, ctrl.attention, ctrl.overlay_cb = titlebar, attention, overlay
    ov.events.loaded += overlay_style
    win.events.closed += lambda: ctrl.quit()
    webview.start(gui="edgechromium")
    ctrl.quit()


def run_browser_fallback(ctrl, url):
    """No pywebview/WebView2: open Edge or Chrome in app mode instead."""
    cands = [os.path.expandvars(p) for p in (
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe")]
    exe = next((c for c in cands if os.path.exists(c)), None)
    if not exe:
        import webbrowser
        webbrowser.open(url)
        threading.Event().wait()
    prof = os.path.join(tempfile.gettempdir(), "FramioProfile")
    p = subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={prof}", "--window-size=1220,820"])
    p.wait()
    ctrl.quit()


def main():
    tune_process()
    ctrl = Controller()
    srv = WebServer(ctrl)
    url = srv.start()
    if "--serve-only" in sys.argv:
        print(f"{url}\nTOKEN={srv.token}", flush=True)
        threading.Event().wait()
    try:
        run_webview(ctrl, url, "--autostart" in sys.argv)
    except Exception as e:       # pywebview or WebView2 missing
        print("webview unavailable:", e, file=sys.stderr)
        run_browser_fallback(ctrl, url)


if __name__ == "__main__":
    main()
