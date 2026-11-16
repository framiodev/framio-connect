"""TeamViewer/AnyDesk-style remote desktop over the local network.

Host streams JPEG frames of the primary screen; viewer sends the same text input protocol
used by keyboard sharing, framed as binary messages.
"""
import hashlib
import io
import socket
import struct
import threading
import time

from .inject import Injector
from .net import (AuthError, client_handshake, keepalive, server_challenge,
                  server_proof, server_verify)

T_FRAME, T_INPUT = 1, 2
MAX_W = 1600
FPS = 12


def send_msg(sock, t, payload):
    sock.sendall(struct.pack(">BI", t, len(payload)) + payload)


def recv_msg(f):
    hdr = f.read(5)
    if len(hdr) < 5:
        return None
    t, n = struct.unpack(">BI", hdr)
    if n > 40_000_000:
        raise ValueError("oversized message")
    data = f.read(n)
    if len(data) < n:
        return None
    return t, data


class RemoteHost:
    def __init__(self, password, port, log, events=None, approve=None, name="PC"):
        self.password, self.port, self.log = password, port, log
        self.events = events or (lambda kind, **kw: None)
        self.approve = approve            # callable(name, ip) -> bool (blocking) or None
        self.running = False
        self.lsock = None
        self.sessions = {}
        self.fail = {}                    # ip -> [timestamps]

    def start(self):
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("0.0.0.0", self.port))
        self.lsock.listen(4)
        self.running = True
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.lsock.close()
        except Exception:
            pass
        for s in list(self.sessions.values()):
            try:
                s["sock"].close()
            except OSError:
                pass

    def disconnect(self, sid):
        s = self.sessions.get(sid)
        if s:
            try:
                s["sock"].close()
            except OSError:
                pass

    def _blocked(self, ip):
        now = time.time()
        lst = [t for t in self.fail.get(ip, []) if now - t < 60]
        self.fail[ip] = lst
        return len(lst) >= 5

    def _accept(self):
        while self.running:
            try:
                s, addr = self.lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(s, addr), daemon=True).start()

    def _emit(self):
        self.events("rsessions", sessions=[
            {"id": k, "name": v["name"], "ip": v["ip"]} for k, v in self.sessions.items()])

    def _serve(self, s, addr):
        ip, sid = addr[0], None
        try:
            keepalive(s)
            if self._blocked(ip):
                s.close()
                return
            s.settimeout(10)
            nonce = server_challenge(s)
            f = s.makefile("rb")
            p = f.readline(512).decode(errors="replace").split()
            # HELLO mac cn name
            if len(p) < 4 or p[0] != "HELLO" or not server_verify(self.password, nonce, p[1]):
                self.fail.setdefault(ip, []).append(time.time())
                s.sendall(b"NO\n")
                self.log("r_bad_password", ip=ip)
                s.close()
                return
            cn, name = p[2], p[3][:32]
            if self.approve is not None and not self.approve(name, ip):
                s.sendall(b"DENIED\n")
                self.log("r_denied", name=name, ip=ip)
                s.close()
                return
            s.sendall(f"OK {server_proof(self.password, nonce, cn)}\n".encode())
            s.settimeout(None)
            sid = f"{ip}:{addr[1]}"
            self.sessions[sid] = {"sock": s, "name": name, "ip": ip}
            self.log("r_started", name=name, ip=ip)
            self._emit()
            alive = threading.Event()
            alive.set()
            threading.Thread(target=self._reader, args=(f, alive, s), daemon=True).start()
            self._stream(s, alive)
        except Exception as e:
            self.log("error", msg=str(e), ip=ip)
        finally:
            if sid:
                self.sessions.pop(sid, None)
                self._emit()
                self.log("r_ended", ip=ip)
            try:
                s.close()
            except OSError:
                pass

    def _reader(self, f, alive, s):
        inj = Injector()
        try:
            while True:
                m = recv_msg(f)
                if m is None:
                    break
                if m[0] == T_INPUT:
                    inj.handle(m[1].decode(errors="replace").split())
        except Exception:
            pass
        finally:
            inj.release_all()
            alive.clear()
            try:
                s.close()
            except OSError:
                pass

    def _stream(self, s, alive):
        from PIL import Image, ImageGrab
        last, last_sent = None, 0.0
        while alive.is_set() and self.running:
            t0 = time.time()
            img = ImageGrab.grab()
            if img.width > MAX_W:
                img = img.resize((MAX_W, int(img.height * MAX_W / img.width)), Image.BILINEAR)
            h = hashlib.blake2b(img.tobytes(), digest_size=8).digest()
            if h != last or t0 - last_sent > 1.5:
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=62)
                send_msg(s, T_FRAME, struct.pack(">HH", img.width, img.height) + buf.getvalue())
                last, last_sent = h, t0
            dt = time.time() - t0
            if dt < 1 / FPS:
                time.sleep(1 / FPS - dt)


class RemoteViewer:
    """Network side of the viewer; the UI polls `latest` for the newest frame."""

    def __init__(self, host, port, password, on_close):
        self.host, self.port, self.password, self.on_close = host, port, password, on_close
        self.sock = None
        self.latest = None        # (w, h, jpeg bytes)
        self.seq = 0
        self.closed = False
        self.error = None
        self.wlock = threading.Lock()

    def connect(self, name):
        s, f = client_handshake(self.host, self.port, self.password, [name],
                                reply_timeout=40)   # host may be waiting for user approval
        s.settimeout(None)
        self.sock = s
        threading.Thread(target=self._recv, args=(f,), daemon=True).start()

    def _recv(self, f):
        try:
            while True:
                m = recv_msg(f)
                if m is None:
                    break
                if m[0] == T_FRAME and len(m[1]) > 4:
                    w, h = struct.unpack(">HH", m[1][:4])
                    self.latest = (w, h, m[1][4:])
                    self.seq += 1
        except Exception as e:
            self.error = str(e)
        self.closed = True
        self.on_close()

    def send_input(self, line):
        if self.closed:
            return
        try:
            with self.wlock:
                send_msg(self.sock, T_INPUT, line.encode())
        except OSError:
            self.close()

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except Exception:
            pass
