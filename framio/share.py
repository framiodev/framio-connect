"""Keyboard/mouse sharing (host = Server, controlled PC = Client) + clipboard + file transfer."""
import base64
import ctypes
import ctypes.wintypes as wt
import secrets
import socket
import threading
import queue
import time

from . import transfer
from .inject import Injector
from .net import (AuthError, client_handshake, keepalive, server_challenge,
                  server_proof, server_verify)
from .winapi import (BTN_MSG, HOOKPROC, KBDLLHOOKSTRUCT, MSLLHOOKSTRUCT, VK_SCROLL,
                     WH_KEYBOARD_LL, WH_MOUSE_LL, WM_MOUSEHWHEEL, WM_MOUSEMOVE,
                     WM_MOUSEWHEEL, WM_QUIT, WM_XBUTTONDOWN, WM_XBUTTONUP,
                     clip_get, clip_set_files, clip_set_text, kernel32, sysmetric, user32)

CLIP_FILE_LIMIT = 300 * 1024 * 1024
TEXT_LIMIT = 1_000_000
LINE_LIMIT = 3_000_000


def _b64(s):
    return base64.b64encode(s[:TEXT_LIMIT].encode("utf-8")).decode()


def _unb64(s):
    return base64.b64decode(s.encode()).decode("utf-8", "replace")


class ClientConn:
    """Server-side handle for one connected, controlled PC."""

    def __init__(self, sock, side, w, h, name, ip):
        self.sock, self.side, self.w, self.h, self.name, self.ip = sock, side, w, h, name, ip
        self.q = queue.Queue(maxsize=5000)
        self.alive = True
        self.last_sig_out = None
        threading.Thread(target=self._send_loop, daemon=True).start()

    def send(self, line):
        try:
            self.q.put_nowait(line)
        except queue.Full:
            pass

    def _send_loop(self):
        while self.alive:
            item = self.q.get()
            if item is None:
                break
            lines = [item]
            try:
                while True:
                    nxt = self.q.get_nowait()
                    if nxt is None:
                        self.alive = False
                        break
                    if nxt[0] == "M" and lines[-1][0] == "M":
                        lines[-1] = nxt          # coalesce consecutive mouse moves
                    else:
                        lines.append(nxt)
            except queue.Empty:
                pass
            try:
                self.sock.sendall(("\n".join(lines) + "\n").encode())
            except OSError:
                break
        self.close()

    def close(self):
        self.alive = False
        try:
            self.q.put_nowait(None)
        except queue.Full:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Server:
    def __init__(self, key, port, log, events=None, clip_text=True, clip_files=True):
        self.key, self.port, self.log = key, port, log
        self.events = events or (lambda kind, **kw: None)
        self.clip_text, self.clip_files = clip_text, clip_files
        self.clients = {}
        self.pending = {}          # token -> (paths, created)
        self.active = None
        self.vx = self.vy = self.cx = self.cy = 0
        self.held = set()
        self.last_leave = 0.0
        self.running = False
        self.lsock = None
        self.tid = None

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self.lsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.lsock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.lsock.bind(("0.0.0.0", self.port))
        self.lsock.listen(8)
        self.running = True
        threading.Thread(target=self._accept_loop, daemon=True).start()
        ready = threading.Event()
        threading.Thread(target=self._hook_thread, args=(ready,), daemon=True).start()
        ready.wait(3)

    def stop(self):
        self.running = False
        self.active = None
        try:
            self.lsock.close()
        except Exception:
            pass
        for c in list(self.clients.values()):
            c.close()
        self.clients.clear()
        if self.tid:
            user32.PostThreadMessageW(self.tid, WM_QUIT, 0, 0)

    def client_list(self):
        return [{"name": c.name, "side": c.side, "ip": c.ip} for c in self.clients.values()]

    def _emit_clients(self):
        self.events("clients", clients=self.client_list())

    # ------------------------------------------------------------ network
    def _accept_loop(self):
        while self.running:
            try:
                s, addr = self.lsock.accept()
            except OSError:
                break
            threading.Thread(target=self._serve, args=(s, addr), daemon=True).start()

    def _serve(self, s, addr):
        conn = None
        try:
            keepalive(s)
            s.settimeout(10)
            nonce = server_challenge(s)
            f = s.makefile("rb")
            p = f.readline(512).decode(errors="replace").split()
            # HELLO mac cn role side w h name
            if len(p) < 8 or p[0] != "HELLO" or not server_verify(self.key, nonce, p[1]):
                s.sendall(b"NO\n")
                self.log("auth_failed", ip=addr[0])
                s.close()
                return
            cn, role, side = p[2], p[3], p[4]
            w, h, name = int(p[5]), int(p[6]), p[7][:32]
            s.sendall(f"OK {server_proof(self.key, nonce, cn)}\n".encode())
            if role == "DATA":
                s.settimeout(120)
                self._serve_data(s, f, name)
                return
            if role != "CTRL" or side not in ("left", "right") or not (0 < w < 20000 and 0 < h < 20000):
                s.close()
                return
            s.settimeout(None)
            conn = ClientConn(s, side, w, h, name, addr[0])
            old = self.clients.get(side)
            self.clients[side] = conn
            if old:
                old.close()
            self.log("connected", name=name, ip=addr[0], side=side)
            self._emit_clients()
            while True:
                line = f.readline(LINE_LIMIT)
                if not line:
                    break
                q = line.decode(errors="replace").split()
                if len(q) == 2 and q[0] == "CT" and self.clip_text:
                    txt = _unb64(q[1])
                    conn.last_sig_out = repr(("text", txt))
                    clip_set_text(txt)
        except Exception as e:
            self.log("error", msg=str(e), ip=addr[0])
        finally:
            if conn:
                if self.active is conn:
                    self.active = None
                    self.events("active", name=None)
                if self.clients.get(conn.side) is conn:
                    del self.clients[conn.side]
                conn.close()
                self.log("disconnected", name=conn.name)
                self._emit_clients()
            else:
                try:
                    s.close()
                except OSError:
                    pass

    def _serve_data(self, s, f, name):
        try:
            q = f.readline(256).decode(errors="replace").split()
            if len(q) >= 1 and q[0] == "PUSH":
                mode = q[1] if len(q) > 1 else "file"
                saved = transfer.recv_files(f, s, transfer.downloads_dir())
                if mode == "clip" and self.clip_files:
                    clip_set_files(saved)
                self.events("files", paths=saved, mode=mode, who=name)
            elif len(q) == 2 and q[0] == "PULL":
                ent = self.pending.pop(q[1], None)
                if ent:
                    transfer.send_files(s, f, ent[0])
                    self.events("pulled", token=q[1])
        except Exception as e:
            self.log("error", msg=str(e), ip=name)
        finally:
            try:
                s.close()
            except OSError:
                pass

    def send_files_to(self, conn, paths, mode="file"):
        now = time.time()
        for k in [k for k, v in self.pending.items() if now - v[1] > 300]:
            del self.pending[k]
        token = secrets.token_hex(8)
        self.pending[token] = (list(paths), now)
        conn.send(f"XF {token} {mode}")
        return token

    # ------------------------------------------------------------ clipboard sync
    def _clip_to(self, conn):
        try:
            c = clip_get()
            if not c or not conn.alive:
                return
            sig = repr(c)
            if sig == conn.last_sig_out:
                return
            if c[0] == "text" and self.clip_text and c[1]:
                conn.last_sig_out = sig
                conn.send("CT " + _b64(c[1]))
            elif c[0] == "files" and self.clip_files and transfer.total_size(c[1]) <= CLIP_FILE_LIMIT:
                conn.last_sig_out = sig
                self.send_files_to(conn, c[1], "clip")
        except Exception as e:
            self.log("error", msg=str(e), ip="clipboard")

    # ------------------------------------------------------------ hooks
    def _hook_thread(self, ready):
        self.tid = kernel32.GetCurrentThreadId()
        self._mp = HOOKPROC(self._mouse)
        self._kp = HOOKPROC(self._kb)
        hmod = kernel32.GetModuleHandleW(None)
        hm = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mp, hmod, 0)
        hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kp, hmod, 0)
        if not hm or not hk:
            self.log("error", msg="hook install failed", ip="")
        ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        for h in (hm, hk):
            if h:
                user32.UnhookWindowsHookEx(h)

    @staticmethod
    def _bounds():
        x0, y0 = sysmetric(76), sysmetric(77)
        return x0, y0, x0 + sysmetric(78) - 1, y0 + sysmetric(79) - 1

    def _enter(self, conn, y):
        x0, y0, x1, y1 = self._bounds()
        self.cx, self.cy = sysmetric(0) // 2, sysmetric(1) // 2
        self.vx = 0 if conn.side == "right" else conn.w - 1
        frac = (y - y0) / max(1, y1 - y0)
        self.vy = min(conn.h - 1, max(0, int(frac * conn.h)))
        self.held.clear()
        self.active = conn
        self._send_pos(conn)
        user32.SetCursorPos(self.cx, self.cy)
        threading.Thread(target=self._clip_to, args=(conn,), daemon=True).start()
        self.events("active", name=conn.name)

    def _leave(self):
        conn = self.active
        if not conn:
            return
        self.active = None
        self.held.clear()
        conn.send("R")
        conn.send("GC")          # ask the PC we are leaving for its clipboard
        x0, y0, x1, y1 = self._bounds()
        y = int(y0 + self.vy / max(1, conn.h - 1) * (y1 - y0))
        user32.SetCursorPos(x1 - 4 if conn.side == "right" else x0 + 4, y)
        self.last_leave = time.time()
        self.events("active", name=None)

    def _send_pos(self, conn):
        nx = int(self.vx * 65535 / max(1, conn.w - 1))
        ny = int(self.vy * 65535 / max(1, conn.h - 1))
        conn.send(f"M {nx} {ny}")

    def _mouse(self, n, w, l):
        try:
            if n >= 0:
                ms = ctypes.cast(l, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (ms.flags & 1):            # skip injected events
                    c = self.active
                    if c is None:
                        if w == WM_MOUSEMOVE and self._check_edge(ms.pt.x, ms.pt.y):
                            return 1
                    else:
                        self._remote_mouse(c, w, ms)
                        return 1
        except Exception as e:
            self.log("error", msg=str(e), ip="mouse")
        return user32.CallNextHookEx(None, n, w, l)

    def _check_edge(self, x, y):
        if not self.clients or time.time() - self.last_leave < 0.4:
            return False
        if user32.GetAsyncKeyState(0x01) & 0x8000 or user32.GetAsyncKeyState(0x02) & 0x8000:
            return False                          # no switching while dragging
        x0, y0, x1, y1 = self._bounds()
        side = "right" if x >= x1 else "left" if x <= x0 else None
        conn = self.clients.get(side) if side else None
        if conn and conn.alive:
            self._enter(conn, y)
            return True
        return False

    def _remote_mouse(self, c, w, ms):
        if w == WM_MOUSEMOVE:
            dx, dy = ms.pt.x - self.cx, ms.pt.y - self.cy
            if dx == 0 and dy == 0:
                return
            self.vx += dx
            self.vy = min(c.h - 1, max(0, self.vy + dy))
            if (c.side == "right" and self.vx < 0) or (c.side == "left" and self.vx > c.w - 1):
                self._leave()
                return
            self.vx = min(c.w - 1, max(0, self.vx))
            self._send_pos(c)
            user32.SetCursorPos(self.cx, self.cy)
        elif w in BTN_MSG:
            b, d = BTN_MSG[w]
            c.send(f"B {b} {d}")
        elif w in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            b = 3 if (ms.mouseData >> 16) == 1 else 4
            c.send(f"B {b} {1 if w == WM_XBUTTONDOWN else 0}")
        elif w == WM_MOUSEWHEEL:
            c.send(f"W {ctypes.c_short(ms.mouseData >> 16).value}")
        elif w == WM_MOUSEHWHEEL:
            c.send(f"H {ctypes.c_short(ms.mouseData >> 16).value}")

    def _kb(self, n, w, l):
        try:
            c = self.active
            if n >= 0 and c is not None:
                kb = ctypes.cast(l, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (kb.flags & 0x10):
                    down = not (kb.flags & 0x80)
                    vk, sc, ext = kb.vkCode, kb.scanCode, kb.flags & 1
                    if vk == VK_SCROLL:
                        if down:
                            self._leave()
                        return 1
                    key = (vk, sc, ext)
                    if down:
                        self.held.add(key)
                    elif key not in self.held:
                        return user32.CallNextHookEx(None, n, w, l)   # pressed locally earlier
                    else:
                        self.held.discard(key)
                    c.send(f"K {vk} {sc} {ext} {1 if down else 0}")
                    return 1
        except Exception as e:
            self.log("error", msg=str(e), ip="keyboard")
        return user32.CallNextHookEx(None, n, w, l)


class Client:
    def __init__(self, host, port, key, side, log, events=None, clip_text=True, clip_files=True):
        self.host, self.port, self.key, self.side = host, port, key, side
        self.log = log
        self.events = events or (lambda kind, **kw: None)
        self.clip_text, self.clip_files = clip_text, clip_files
        self.stop_flag = False
        self.sock = None
        self.connected = False
        self.inj = Injector()
        self.last_sig_out = None
        self.wlock = threading.Lock()

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self.stop_flag = True
        try:
            self.sock.close()
        except Exception:
            pass
        self.inj.release_all()

    def _fields(self, role):
        name = (socket.gethostname() or "pc").replace(" ", "_")
        return [role, self.side, str(sysmetric(0)), str(sysmetric(1)), name]

    def _run(self):
        while not self.stop_flag:
            try:
                self.events("state", state="connecting")
                self._session()
            except AuthError as e:
                if not self.stop_flag:
                    self.log("auth_rejected", msg=str(e))
            except Exception as e:
                if not self.stop_flag:
                    self.log("no_connection", msg=str(e))
            self.connected = False
            self.inj.release_all()
            self.events("state", state="disconnected")
            for _ in range(20):
                if self.stop_flag:
                    return
                time.sleep(0.1)

    def _session(self):
        s, f = client_handshake(self.host, self.port, self.key, self._fields("CTRL"))
        self.sock = s
        s.settimeout(None)
        self.connected = True
        self.log("connected_host")
        self.events("state", state="connected")
        while not self.stop_flag:
            line = f.readline(LINE_LIMIT)
            if not line:
                break
            self._handle(line.decode(errors="replace").split())

    def send_line(self, line):
        with self.wlock:
            self.sock.sendall((line + "\n").encode())

    def _handle(self, p):
        if not p or self.inj.handle(p):
            return
        t = p[0]
        try:
            if t == "CT" and self.clip_text:
                txt = _unb64(p[1])
                self.last_sig_out = repr(("text", txt))
                clip_set_text(txt)
            elif t == "XF":
                threading.Thread(target=self._pull, args=(p[1], p[2]), daemon=True).start()
            elif t == "GC":
                threading.Thread(target=self._clip_to_host, daemon=True).start()
        except (IndexError, ValueError):
            pass

    def _pull(self, token, mode):
        try:
            s, f = client_handshake(self.host, self.port, self.key, self._fields("DATA"))
            try:
                s.settimeout(120)
                s.sendall(f"PULL {token}\n".encode())
                saved = transfer.recv_files(f, s, transfer.downloads_dir())
            finally:
                s.close()
            if mode == "clip" and self.clip_files:
                clip_set_files(saved)
            self.events("files", paths=saved, mode=mode, who="host")
        except Exception as e:
            self.log("error", msg=str(e))

    def _clip_to_host(self):
        try:
            c = clip_get()
            if not c:
                return
            sig = repr(c)
            if sig == self.last_sig_out:
                return
            if c[0] == "text" and self.clip_text and c[1]:
                self.last_sig_out = sig
                self.send_line("CT " + _b64(c[1]))
            elif c[0] == "files" and self.clip_files and transfer.total_size(c[1]) <= CLIP_FILE_LIMIT:
                self.last_sig_out = sig
                self.push_files(c[1], "clip")
        except Exception as e:
            self.log("error", msg=str(e))

    def push_files(self, paths, mode="file", progress=None):
        s, f = client_handshake(self.host, self.port, self.key, self._fields("DATA"))
        try:
            s.settimeout(120)
            s.sendall(f"PUSH {mode}\n".encode())
            transfer.send_files(s, f, paths, progress)
        finally:
            s.close()
