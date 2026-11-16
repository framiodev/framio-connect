"""Settings persistence, autostart (HKCU Run) and per-user install."""
import json
import locale
import os
import secrets
import shutil
import socket
import subprocess
import sys

from . import APP_ID, APP_NAME, REMOTE_PORT, SHARE_PORT

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_ID)
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTALL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Framio", "Connect")


def _alnum(n):
    a = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(a) for _ in range(n))


def default_lang():
    try:
        loc = (locale.getdefaultlocale()[0] or "").lower()
    except Exception:
        loc = ""
    return "tr" if loc.startswith("tr") else "en"


def defaults():
    return {
        "lang": default_lang(), "theme": "dark",
        "mode": "host", "key": _alnum(8), "port": SHARE_PORT, "host": "", "side": "right",
        "clip_text": True, "clip_files": True,
        "share_running": False,
        "device_id": "".join(secrets.choice("0123456789") for _ in range(9)),
        "device_name": socket.gethostname(),
        "remote_enabled": False, "remote_password": _alnum(8), "remote_approval": True,
        "remote_port": REMOTE_PORT,
        "auto_update": True,
    }


def load():
    cfg = defaults()
    try:
        with open(os.path.join(APPDATA, "config.json"), "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


def save(cfg):
    os.makedirs(APPDATA, exist_ok=True)
    with open(os.path.join(APPDATA, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)


def fmt_id(i):
    i = str(i)
    return " ".join(i[k:k + 3] for k in range(0, len(i), 3))


# ---- autostart / install (no admin rights needed)
def exe_path():
    return sys.executable if getattr(sys, "frozen", False) else None


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_ID)
            return True
    except OSError:
        return False


def set_autostart(on):
    import winreg
    exe = exe_path()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            if not exe:
                raise RuntimeError("only available in the packaged .exe")
            winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, f'"{exe}" --autostart')
        else:
            try:
                winreg.DeleteValue(k, APP_ID)
            except OSError:
                pass


def install_here():
    """Copy the exe to %LOCALAPPDATA%\\Framio\\Connect, add Start Menu shortcut + autostart.
    Returns the installed exe path."""
    exe = exe_path()
    if not exe:
        raise RuntimeError("only available in the packaged .exe")
    os.makedirs(INSTALL_DIR, exist_ok=True)
    dst = os.path.join(INSTALL_DIR, APP_ID + ".exe")
    if os.path.normcase(exe) != os.path.normcase(dst):
        shutil.copy2(exe, dst)
    lnk = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs", APP_NAME + ".lnk")
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('%s');"
          "$s.TargetPath='%s';$s.WorkingDirectory='%s';$s.Save()" % (lnk, dst, INSTALL_DIR))
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                   creationflags=0x08000000)
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, APP_ID, 0, winreg.REG_SZ, f'"{dst}" --autostart')
    return dst


def firewall_command(share_port, remote_port, discovery_port):
    """netsh command line (to be run elevated) that opens the ports Framio needs."""
    exe = exe_path() or "python.exe"
    rules = []
    for name, proto, port in (("Share", "TCP", share_port), ("Remote", "TCP", remote_port),
                              ("Discovery", "UDP", discovery_port)):
        rn = f"{APP_NAME} {name}"
        rules.append(f'netsh advfirewall firewall delete rule name="{rn}" >nul & '
                     f'netsh advfirewall firewall add rule name="{rn}" dir=in action=allow '
                     f'protocol={proto} localport={port} program="{exe}" profile=private,domain')
    return "/c " + " & ".join(rules)
