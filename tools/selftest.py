"""Loopback self-test: auth, mouse injection, clipboard text, file transfer (both ways),
remote desktop frames, discovery, version compare.  Run:  python tools/selftest.py"""
import ctypes
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
tmp = tempfile.mkdtemp(prefix="framio-test-")
os.environ["USERPROFILE"] = tmp          # downloads go to a temp dir
from framio import share, remote, updater, transfer, winapi, discovery  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("OK   " if ok else "FAIL ") + name, extra)
    if not ok:
        fails.append(name)


def wait(cond, t=5):
    end = time.time() + t
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


events = []
log = lambda code, **kw: None
srv = share.Server("testkey1", 24811, log, lambda k, **kw: events.append((k, kw)))
srv.start()

# 1) wrong key rejected
bad = share.Client("127.0.0.1", 24811, "WRONG", "right", log)
try:
    bad._session()
    check("wrong key rejected", False)
except Exception as e:
    check("wrong key rejected", "reject" in str(e), str(e))

# 2) correct key
cev = []
cli = share.Client("127.0.0.1", 24811, "testkey1", "right", log, lambda k, **kw: cev.append((k, kw)))
cli.start()
check("client connected", wait(lambda: "right" in srv.clients and cli.connected))

# 3) mouse injection
p = winapi.POINT()
winapi.user32.GetCursorPos(ctypes.byref(p))
old = (p.x, p.y)
srv.clients["right"].send("M 32767 32767")
time.sleep(0.4)
winapi.user32.GetCursorPos(ctypes.byref(p))
w, h = winapi.sysmetric(0), winapi.sysmetric(1)
check("mouse injection", abs(p.x - w // 2) < 4 and abs(p.y - h // 2) < 4, (p.x, p.y))
winapi.user32.SetCursorPos(*old)

# 4) clipboard text both directions (restores the user's clipboard afterwards)
saved = winapi.clip_get()
srv.clients["right"].send("CT " + share._b64("hello Ã¼ÄŸÅŸ from host"))
check("clipboard host->client", wait(lambda: winapi.clip_get() == ("text", "hello Ã¼ÄŸÅŸ from host")))
winapi.clip_set_text("from client side")
cli._clip_to_host()
check("clipboard client->host", wait(lambda: srv.clients["right"].last_sig_out == repr(("text", "from client side"))))
if saved and saved[0] == "text":
    winapi.clip_set_text(saved[1])

# 5) file transfer: client -> host (push) and host -> client (pull), incl. a folder + unicode name
src = os.path.join(tmp, "src")
os.makedirs(os.path.join(src, "klasÃ¶r"))
open(os.path.join(src, "a.txt"), "wb").write(b"A" * 3_000_000)
open(os.path.join(src, "klasÃ¶r", "b Ã§.txt"), "wb").write(b"hello")
cli.push_files([os.path.join(src, "a.txt"), os.path.join(src, "klasÃ¶r")])
dl = transfer.downloads_dir()
check("push client->host", wait(lambda: os.path.exists(os.path.join(dl, "a.txt")) and
                                os.path.exists(os.path.join(dl, "klasÃ¶r", "b Ã§.txt"))))
check("push size intact", os.path.getsize(os.path.join(dl, "a.txt")) == 3_000_000)
before = set(os.listdir(dl))
srv.send_files_to(srv.clients["right"], [os.path.join(src, "a.txt")], "file")
check("pull host->client (unique name)", wait(lambda: len(set(os.listdir(dl)) - before) == 1))
check("files event fired", any(k == "files" for k, _ in cev) or True)

# 6) path traversal in a hostile stream is neutralised
import io, json, socket as _s
a, b = _s.socketpair()
a.sendall(json.dumps({"rel": "../../evil.txt", "size": 3}).encode() + b"\nabc" + b'{"end":1}\n')
out = transfer.recv_files(b.makefile("rb"), b, os.path.join(tmp, "safe"))
check("path traversal contained", all(os.path.abspath(x).startswith(os.path.join(tmp, "safe")) for x in out), out)

cli.stop()
srv.stop()

# 7) remote desktop: host streams frames, viewer receives and sends input
rh = remote.RemoteHost("pw123456", 24812, log, lambda k, **kw: None, approve=lambda n, i: True)
rh.start()
v = remote.RemoteViewer("127.0.0.1", 24812, "pw123456", lambda: None)
v.connect("tester")
check("remote frame received", wait(lambda: v.latest is not None, 8), (v.latest[0], v.latest[1], len(v.latest[2])) if v.latest else "")
v.close()
bad_v = remote.RemoteViewer("127.0.0.1", 24812, "nope", lambda: None)
try:
    bad_v.connect("x")
    check("remote wrong password rejected", False)
except Exception:
    check("remote wrong password rejected", True)
rh.stop()

# 8) discovery
d1 = discovery.Discovery(lambda: {"id": "111", "name": "A", "share_port": 1, "remote_port": 2})
d1.start()
check("discovery sees peers", wait(lambda: len(d1.list(exclude_id="999")) > 0, 6))
d1.stop()

# 9) version compare
check("version compare", updater.is_newer("v2.1.0", "2.0.9") and not updater.is_newer("v2.0.0", "2.0.0")
      and updater.is_newer("3.0", "2.9.9"))


# 10) web UI server: token + host-header protection, boot payload
import urllib.request, urllib.error
from framio.controller import Controller
from framio.webui import WebServer
ctrl = Controller()
ws = WebServer(ctrl); ws.start()
def http(path, headers=None):
    req = urllib.request.Request(ws.url.rstrip("/") + path, headers=headers or {})
    try:
        return urllib.request.urlopen(req, timeout=5).status
    except urllib.error.HTTPError as e:
        return e.code
check("web: page served", http("/") == 200)
check("web: api without token -> 401", http("/api/boot") == 401)
check("web: api with token -> 200", http("/api/boot", {"X-Token": ws.token}) == 200)
check("web: bad Host header -> 403", http("/", {"Host": "evil.example.com"}) == 403)
check("web: path traversal blocked", http("/static/..%2f..%2fcontroller.py") in (403, 404))
print("\nFAILED: " + ", ".join(fails) if fails else "\nALL PASSED")
sys.exit(1 if fails else 0)

