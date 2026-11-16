"""Framio Connect launcher.

  python main.py                 desktop window (WebView2)
  python main.py --serve-only    just serve the UI and print the URL (development / browser testing)
  python main.py --autostart     start minimised (used by the Windows startup entry)
"""
import ctypes
import os
import subprocess
import sys
import tempfile
import threading
import time

from framio import APP_NAME
from framio.controller import Controller
from framio.webui import WebServer
from framio.winapi import set_dark_titlebar

TITLE = APP_NAME


def _hwnd():
    return ctypes.windll.user32.FindWindowW(None, TITLE)


def run_webview(ctrl, url, minimized):
    import webview
    win = webview.create_window(TITLE, url, width=1220, height=820, min_size=(1000, 700),
                                background_color="#0A1020", minimized=minimized)

    def titlebar(dark):
        h = _hwnd()
        if h:
            set_dark_titlebar(h, dark)

    def attention():
        try:
            win.restore()
            h = _hwnd()
            if h:
                ctypes.windll.user32.SetForegroundWindow(h)
        except Exception:
            pass

    ctrl.titlebar, ctrl.attention = titlebar, attention
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
    ctrl = Controller()
    srv = WebServer(ctrl)
    url = srv.start()
    if "--serve-only" in sys.argv:
        print(f"{url}?dev=1\nTOKEN={srv.token}", flush=True)
        # dev mode: print a URL that embeds the token via the normal index route
        threading.Event().wait()
    try:
        run_webview(ctrl, url, "--autostart" in sys.argv)
    except Exception as e:       # pywebview or WebView2 missing
        print("webview unavailable:", e, file=sys.stderr)
        run_browser_fallback(ctrl, url)


if __name__ == "__main__":
    main()
