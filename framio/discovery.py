"""LAN device discovery via UDP broadcast (no secrets are broadcast)."""
import json
import socket
import threading
import time

from . import DISCOVERY_PORT
from .net import local_ips

MAGIC = "framio-connect/1"


class Discovery:
    def __init__(self, info_fn):
        self.info_fn = info_fn          # -> dict(id, name, share_port|None, remote_port|None)
        self.peers = {}
        self.running = False
        self.sock = None

    def start(self):
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        try:
            self.sock.bind(("", DISCOVERY_PORT))
        except OSError:
            return
        threading.Thread(target=self._listen, daemon=True).start()
        threading.Thread(target=self._announce, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            self.sock.close()
        except Exception:
            pass

    def _targets(self):
        t = {"255.255.255.255"}
        for ip in local_ips():
            a = ip.split(".")
            t.add(".".join(a[:3] + ["255"]))
        return t

    def _announce(self):
        while self.running:
            try:
                msg = dict(self.info_fn(), app=MAGIC)
                data = json.dumps(msg).encode()
                for t in self._targets():
                    try:
                        self.sock.sendto(data, (t, DISCOVERY_PORT))
                    except OSError:
                        pass
            except Exception:
                pass
            for _ in range(30):
                if not self.running:
                    return
                time.sleep(0.1)

    def _listen(self):
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
                m = json.loads(data.decode())
                if m.get("app") != MAGIC:
                    continue
                m["ip"] = addr[0]
                m["seen"] = time.time()
                self.peers[str(m.get("id"))] = m
            except (OSError, ValueError):
                if not self.running:
                    break

    def list(self, exclude_id=None):
        now = time.time()
        return [p for k, p in self.peers.items()
                if now - p["seen"] < 12 and k != str(exclude_id)]
