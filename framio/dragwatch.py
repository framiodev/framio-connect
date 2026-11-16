"""Detects a file drag that is being pushed against a screen edge that faces another PC.

When that happens the app shows a "drop here to send to <PC>" panel on that edge (a normal OS drop
target), so the user can finish the drag on it. Works for any Explorer/desktop drag, in both
directions (host -> client, and on a controlled PC -> host).

A drag is recognised by either signal:
  1. the shell's drag-image window ("SysDragImage") is visible, or
  2. the left button went down on a shell list view (Explorer file list / desktop icons) and the
     pointer has since moved far enough - window title-bar drags and scrollbar drags do not match.
"""
import ctypes
import ctypes.wintypes as wt
import math
import os
import threading
import time

from .winapi import POINT, sysmetric, user32

user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowW.restype = ctypes.c_void_p
user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = ctypes.c_void_p
user32.GetClassNameW.argtypes = [ctypes.c_void_p, wt.LPWSTR, ctypes.c_int]

EDGE = 40          # px from the screen edge that arms the panel
MOVE_MIN = 30      # px the pointer must travel after the press (signal 2)
SHELL_LEAF = {"SysListView32", "DirectUIHWND", "SHELLDLL_DefView"}


def _leaf_class(pt):
    h = user32.WindowFromPoint(POINT(pt.x, pt.y))
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 64)
    return buf.value


def drag_image_visible():
    if os.environ.get("FRAMIO_FORCE_DRAG"):         # test hook: treat any held button as a drag
        return True
    h = user32.FindWindowW("SysDragImage", None)
    return bool(h) and bool(user32.IsWindowVisible(h))


class DragWatcher:
    def __init__(self, candidates, show, hide):
        self.candidates, self.show, self.hide = candidates, show, hide
        self.shown = None

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        last, was_down = 0.0, False
        press = (0, 0)
        press_shell = False
        pt = POINT()
        while True:
            time.sleep(0.03)
            try:
                down = bool(user32.GetAsyncKeyState(0x01) & 0x8000)
                now = time.time()
                user32.GetCursorPos(ctypes.byref(pt))
                if down and not was_down:                       # a new press: remember where / on what
                    press = (pt.x, pt.y)
                    press_shell = _leaf_class(pt) in SHELL_LEAF
                was_down = down
                if not down:
                    if self.shown and now - last > 1.2:         # leave time for the drop to be handled
                        self.hide()
                        self.shown = None
                    continue
                cands = self.candidates()
                if not cands:
                    if self.shown:
                        self.hide()
                        self.shown = None
                    continue
                x0 = sysmetric(76)
                x1 = x0 + sysmetric(78) - 1
                if self.shown is None:
                    for c in cands:
                        near = pt.x >= x1 - EDGE if c["edge"] == "right" else pt.x <= x0 + EDGE
                        if not near:
                            continue
                        moved = math.hypot(pt.x - press[0], pt.y - press[1]) >= MOVE_MIN
                        if drag_image_visible() or (press_shell and moved):
                            self.show(c)
                            self.shown, last = c, now
                            break
                else:
                    last = now                                  # drag still held: keep the drop zone up
            except Exception:
                pass
