"""Self-update from GitHub Releases.

Release assets expected:  FramioConnect.exe  and  FramioConnect.exe.sha256
The download is verified against the published SHA-256 before it replaces the running exe.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request

from . import APP_ID, GITHUB_REPO, __version__

EXE_NAME = APP_ID + ".exe"
API = "https://api.github.com/repos/{repo}/releases/latest"


def parse_version(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(x) for x in nums[:3]) + (0,) * (3 - len(nums[:3]))


def is_newer(remote, local=__version__):
    return parse_version(remote) > parse_version(local)


def _get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_ID}/{__version__}",
                                               "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def check(repo=GITHUB_REPO):
    """Return dict(version, exe_url, sha_url, notes) if a newer release exists, else None."""
    if repo.startswith("OWNER/"):
        return None
    with _get(API.format(repo=repo)) as r:
        rel = json.load(r)
    tag = rel.get("tag_name", "")
    if not is_newer(tag):
        return None
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    if EXE_NAME not in assets or EXE_NAME + ".sha256" not in assets:
        return None
    return {"version": tag.lstrip("v"), "exe_url": assets[EXE_NAME],
            "sha_url": assets[EXE_NAME + ".sha256"], "notes": rel.get("body") or ""}


def download(info, progress=None):
    """Download + verify. Returns the path of the verified new exe."""
    with _get(info["sha_url"]) as r:
        want = r.read(512).decode().split()[0].lower()
    d = tempfile.mkdtemp(prefix="framio-update-")
    path = os.path.join(d, EXE_NAME)
    for attempt in range(3):            # a truncated/corrupt download is retried, never installed
        h = hashlib.sha256()
        with _get(info["exe_url"], timeout=60) as r, open(path, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            got = 0
            while True:
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(got / total)
        if h.hexdigest() == want and (not total or got == total):
            return path
    os.remove(path)
    raise RuntimeError("checksum mismatch - update discarded")


def apply_and_restart(new_exe, extra_args="--autostart"):
    """Swap the running exe after we exit, then relaunch. Caller must exit right after."""
    if not getattr(sys, "frozen", False):
        raise RuntimeError("not a frozen build")
    target = sys.executable
    bat = os.path.join(os.path.dirname(new_exe), "apply.bat")
    with open(bat, "w") as f:
        f.write(
            "@echo off\r\n"
            "set n=0\r\n"
            ":retry\r\n"
            "ping -n 2 127.0.0.1 >nul\r\n"
            f'move /y "{new_exe}" "{target}" >nul 2>&1\r\n'
            "if errorlevel 1 (\r\n"
            "  set /a n+=1\r\n"
            "  if %n% lss 30 goto retry\r\n"
            ")\r\n"
            f'start "" "{target}" {extra_args}\r\n'
            'del "%~f0"\r\n')
    subprocess.Popen(["cmd", "/c", bat], creationflags=0x08000000 | 0x00000008, close_fds=True)
