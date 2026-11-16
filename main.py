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

user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.IsIconic.argtypes = [ctypes.c_void_p]
user32.IsZoomed.argtypes = [ctypes.c_void_p]
user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.RECT)]
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

    saved = {}

    def overlay(action, edge=None):
        """Drag held against an edge: bring THIS window up against that edge (without stealing focus,
        which would disturb the drag); put it back afterwards."""
        h = _hwnd(TITLE)
        if not h:
            return
        if action == "show":
            if saved:
                return
            r = wt.RECT()
            user32.GetWindowRect(h, ctypes.byref(r))
            saved.update(rect=(r.left, r.top, r.right - r.left, r.bottom - r.top),
                         iconic=bool(user32.IsIconic(h)), zoomed=bool(user32.IsZoomed(h)))
            if saved["iconic"]:
                user32.ShowWindow(h, 4)                                    # SW_SHOWNOACTIVATE
                user32.GetWindowRect(h, ctypes.byref(r))
            w, hh = r.right - r.left, r.bottom - r.top
            x0, vw = sysmetric(76), sysmetric(78)
            x = r.left
            if not saved["zoomed"]:
                x = x0 + vw - w if edge == "right" else x0
            user32.SetWindowPos(h, -1, x, r.top, w, hh, 0x10 | 0x40)    # TOPMOST, NOACTIVATE, SHOW
        elif saved:
            x, y, w, hh = saved["rect"]
            if saved["zoomed"]:
                user32.SetWindowPos(h, -2, 0, 0, 0, 0, 0x10 | 0x2 | 0x1)  # just drop TOPMOST
            else:
                user32.SetWindowPos(h, -2, x, y, w, hh, 0x10)
            if saved["iconic"]:
                user32.ShowWindow(h, 6)                                    # SW_MINIMIZE
            saved.clear()

    ctrl.titlebar, ctrl.attention, ctrl.overlay_cb = titlebar, attention, overlay
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
    test_update = os.environ.get("FRAMIO_TEST_UPDATE")      # test hook: run the swap-and-restart flow
    if test_update:
        def _swap():
            from framio import updater
            updater.apply_and_restart(test_update)
            ctrl.quit()
        threading.Timer(8, _swap).start()
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
