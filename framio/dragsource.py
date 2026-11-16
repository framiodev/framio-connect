"""Starts a real shell drag of files, so the user can finish the drop anywhere on this PC.

Used when files arrive in "drag" mode: a tiny invisible window is created under the cursor, a
synthetic left-button press starts OLE DoDragDrop with a file-drop data object, and the drag then
follows the (remotely forwarded) cursor until the next left-button release - which performs the
drop on whatever is under the cursor (an Explorer folder, the desktop, a chat window ...).
Esc cancels. Needs pythonnet (always present together with the pywebview desktop window).
"""
import ctypes
import threading

from .winapi import POINT, mouse_input, user32

LEFT_DOWN, LEFT_UP = 0x0002, 0x0004
DEBUG = []                                  # filled with step names (used by tests)
TIMEOUT_MS = 120000


def available():
    try:
        import clr  # noqa: F401
        return True
    except Exception:
        return False


def start_drag(paths, on_done=None):
    """Begin the drag asynchronously. Returns False if it cannot be started on this system."""
    paths = [p for p in paths if p]
    if not paths:
        return False
    try:
        import clr
        clr.AddReference("System.Windows.Forms")
        clr.AddReference("System.Drawing")
        from System.Collections.Specialized import StringCollection
        from System.Drawing import Point, Size
        from System.Threading import ApartmentState, Thread, ThreadStart
        from System.Windows.Forms import (Application, DataObject, DragDropEffects, Form,
                                          FormBorderStyle, FormStartPosition, Timer)
    except Exception:
        return False

    result = {"effect": None}

    def run():
        form = Form()
        form.FormBorderStyle = getattr(FormBorderStyle, "None")
        form.ShowInTaskbar = False
        form.TopMost = True
        form.StartPosition = FormStartPosition.Manual
        form.Size = Size(16, 16)
        form.Opacity = 0.02                       # invisible, but still receives the mouse press
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        form.Location = Point(pt.x - 8, pt.y - 8)

        def begin(sender, e):
            DEBUG.append("mousedown")
            data = DataObject()
            col = StringCollection()
            for p in paths:
                col.Add(p)
            data.SetFileDropList(col)
            try:
                result["effect"] = str(sender.DoDragDrop(data, DragDropEffects.Copy))
            except Exception:
                pass
            finally:
                if user32.GetAsyncKeyState(0x01) & 0x8000:
                    mouse_input(LEFT_UP)          # never leave the button logically stuck down
                sender.Close()

        press = Timer()
        press.Interval = 250

        def do_press(sender, e):
            press.Stop()
            DEBUG.append("press")
            form.Activate()
            mouse_input(LEFT_DOWN)                # the press lands on our little window -> MouseDown

        def shown(sender, e):
            DEBUG.append("shown")
            press.Tick += do_press                # give the window a moment to be fully on screen
            press.Start()

        def timeout(sender, e):
            mouse_input(LEFT_UP)                  # gives up: ends the drag, closes the window

        form.MouseDown += begin
        form.Shown += shown
        tm = Timer()
        tm.Interval = TIMEOUT_MS
        tm.Tick += timeout
        tm.Start()
        Application.Run(form)
        tm.Stop()
        if on_done:
            on_done(result["effect"])

    t = Thread(ThreadStart(run))
    t.SetApartmentState(ApartmentState.STA)
    t.IsBackground = True
    t.Start()
    return True
