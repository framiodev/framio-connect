"""Thin ctypes layer over the Win32 APIs we need (input hooks, SendInput, clipboard)."""
import ctypes
import ctypes.wintypes as wt
import struct
import time

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

ULONG_PTR = ctypes.c_size_t
LRESULT = ctypes.c_ssize_t


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long), ("mouseData", wt.DWORD),
                ("dwFlags", wt.DWORD), ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wt.DWORD), ("wParamL", wt.WORD), ("wParamH", wt.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wt.DWORD), ("u", _INPUTUNION)]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)

user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wt.DWORD]
user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t]
user32.CallNextHookEx.restype = LRESULT
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendInput.argtypes = [wt.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.MapVirtualKeyW.argtypes = [wt.UINT, wt.UINT]
user32.MapVirtualKeyW.restype = wt.UINT
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = ctypes.c_void_p

user32.OpenClipboard.argtypes = [wt.HWND]
user32.OpenClipboard.restype = wt.BOOL
user32.GetClipboardData.argtypes = [wt.UINT]
user32.GetClipboardData.restype = ctypes.c_void_p
user32.SetClipboardData.argtypes = [wt.UINT, ctypes.c_void_p]
user32.SetClipboardData.restype = ctypes.c_void_p
user32.IsClipboardFormatAvailable.argtypes = [wt.UINT]
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wt.UINT, wt.LPWSTR, wt.UINT]
shell32.DragQueryFileW.restype = wt.UINT

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
WM_MOUSEMOVE = 0x200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x201, 0x202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x204, 0x205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x207, 0x208
WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEHWHEEL = 0x20A, 0x20B, 0x20C, 0x20E
VK_SCROLL = 0x91
CF_UNICODETEXT, CF_HDROP = 13, 15

BTN_MSG = {
    WM_LBUTTONDOWN: (0, 1), WM_LBUTTONUP: (0, 0),
    WM_RBUTTONDOWN: (1, 1), WM_RBUTTONUP: (1, 0),
    WM_MBUTTONDOWN: (2, 1), WM_MBUTTONUP: (2, 0),
}
BTN_FLAGS = {0: (0x0002, 0x0004), 1: (0x0008, 0x0010), 2: (0x0020, 0x0040)}
XBUTTON_DOWN, XBUTTON_UP = 0x0080, 0x0100
EXTENDED_VKS = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E,
                0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5}


def sysmetric(i):
    return user32.GetSystemMetrics(i)


def mouse_input(flags, dx=0, dy=0, data=0):
    i = INPUT()
    i.type = 0
    i.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def key_input(vk, sc, ext, down):
    if not sc:
        sc = user32.MapVirtualKeyW(vk, 0)
    i = INPUT()
    i.type = 1
    i.u.ki = KEYBDINPUT(vk, sc, (1 if ext else 0) | (0 if down else 2), 0, 0)
    user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


# ----------------------------------------------------------------- clipboard
def _open_clip():
    for _ in range(15):
        if user32.OpenClipboard(None):
            return True
        time.sleep(0.03)
    return False


def clip_get():
    """Return ('text', str) / ('files', [paths]) / None."""
    if not _open_clip():
        return None
    try:
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            h = user32.GetClipboardData(CF_HDROP)
            if h:
                n = shell32.DragQueryFileW(h, 0xFFFFFFFF, None, 0)
                files = []
                for i in range(n):
                    ln = shell32.DragQueryFileW(h, i, None, 0)
                    buf = ctypes.create_unicode_buffer(ln + 1)
                    shell32.DragQueryFileW(h, i, buf, ln + 1)
                    files.append(buf.value)
                if files:
                    return ("files", files)
        if user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if h:
                p = kernel32.GlobalLock(h)
                try:
                    return ("text", ctypes.wstring_at(p))
                finally:
                    kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return None


def _clip_put(fmt, data):
    if not _open_clip():
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(0x0002, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        return bool(user32.SetClipboardData(fmt, h))
    finally:
        user32.CloseClipboard()


def clip_set_text(s):
    return _clip_put(CF_UNICODETEXT, (s + "\0").encode("utf-16-le"))


def clip_set_files(paths):
    hdr = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # DROPFILES, wide chars
    body = ("\0".join(paths) + "\0\0").encode("utf-16-le")
    return _clip_put(CF_HDROP, hdr + body)


# ----------------------------------------------------------------- misc
def set_dark_titlebar(hwnd, dark):
    try:
        v = ctypes.c_int(1 if dark else 0)
        for attr in (20, 19):
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd), attr, ctypes.byref(v), ctypes.sizeof(v))
    except Exception:
        pass


def run_elevated(exe, params):
    """Launch exe elevated (one UAC prompt). Returns True if the launch was accepted."""
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
    return r > 32


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False
