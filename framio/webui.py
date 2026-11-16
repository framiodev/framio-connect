"""Loopback-only HTTP server that serves the UI and bridges it to the Controller.

Security: binds 127.0.0.1 only, validates the Host header (anti DNS-rebinding), requires a random
per-run token on every API call (custom header => no cross-site requests), sends no CORS headers.
"""
import json
import mimetypes
import os
import queue
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import i18n

WEB_DIR = (os.path.join(sys._MEIPASS, "framio", "web") if hasattr(sys, "_MEIPASS")
           else os.path.join(os.path.dirname(os.path.abspath(__file__)), "web"))

CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")


def make_handler(ctrl, token, port_holder):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        # ---- helpers
        def _host_ok(self):
            p = port_holder[0]
            return self.headers.get("Host", "") in (f"127.0.0.1:{p}", f"localhost:{p}")

        def _authed(self, q):
            t = self.headers.get("X-Token") or (q.get("t") or [""])[0]
            return secrets.compare_digest(t.encode(), token.encode())

        def _send(self, code, body=b"", ctype="application/json", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, default=str).encode())

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            path = unquote(u.path)
            if path in ("/", "/overlay"):
                with open(os.path.join(WEB_DIR, "index.html" if path == "/" else "overlay.html"), "r", encoding="utf-8") as f:
                    html = f.read().replace("__TOKEN__", token)
                return self._send(200, html.encode(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": CSP})
            if path.startswith("/static/"):
                fp = os.path.realpath(os.path.join(WEB_DIR, path[len("/static/"):]))
                if not fp.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(fp):
                    return self._send(404, b"not found", "text/plain")
                with open(fp, "rb") as f:
                    data = f.read()
                ct = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                return self._send(200, data, ct)
            if not self._authed(q):
                return self._send(401, b"unauthorized", "text/plain")
            if path == "/api/boot":
                return self._json({"state": ctrl.state(), "strings": i18n.S, "logs": ctrl.logs_snapshot()})
            if path == "/api/events":
                return self._sse()
            if path == "/stream":
                return self._mjpeg((q.get("v") or [""])[0])
            self._send(404, b"not found", "text/plain")

        def _sse(self):
            qq = queue.Queue()
            ctrl.subs.append(qq)
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                while True:
                    try:
                        msg = qq.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (OSError, ValueError):
                pass
            finally:
                if qq in ctrl.subs:
                    ctrl.subs.remove(qq)

        def _mjpeg(self, vid):
            import time
            v = ctrl.viewers.get(vid)
            if not v:
                return self._send(404, b"no viewer", "text/plain")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                last = -1
                while not v.closed:
                    fr = v.latest
                    if fr and v.seq != last:
                        last = v.seq
                        jpg = fr[2]
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(jpg)).encode() + b"\r\n\r\n" + jpg + b"\r\n")
                        self.wfile.flush()
                    else:
                        time.sleep(0.008)
            except OSError:
                pass

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._host_ok():
                return self._send(403, b"forbidden", "text/plain")
            if not self._authed(q):
                return self._send(401, b"unauthorized", "text/plain")
            n = int(self.headers.get("Content-Length") or 0)
            path = unquote(u.path)
            try:
                if path == "/api/upload":
                    return self._upload(q, n)
                body = self.rfile.read(n) if n else b""
                if path == "/api/rinput":
                    d = json.loads(body or b"{}")
                    ctrl.rinput(d.get("v", ""), d.get("text", ""))
                    return self._json(True)
                if path.startswith("/api/call/"):
                    d = json.loads(body or b"{}")
                    return self._json(ctrl.call(path[len("/api/call/"):], d.get("args", [])))
            except KeyError:
                return self._json({"error": "unknown"}, 404)
            except Exception as e:
                return self._json({"error": str(e)}, 500)
            self._send(404, b"not found", "text/plain")

        def _upload(self, q, n):
            dest = ctrl.outbox_path((q.get("b") or [""])[0], (q.get("rel") or [""])[0])
            if not dest:
                self.rfile.read(n)
                return self._json({"error": "bad path"}, 400)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                left = n
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            self._json(True)

    return H


class WebServer:
    def __init__(self, ctrl):
        self.token = secrets.token_urlsafe(24)
        self.port_holder = [0]
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctrl, self.token, self.port_holder))
        self.httpd.daemon_threads = True
        self.port_holder[0] = self.httpd.server_address[1]

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port_holder[0]}/"

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.url
