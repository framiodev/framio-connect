"""UI-independent application controller: owns the services, the state and every action."""
import json
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from . import DISCOVERY_PORT, GITHUB_REPO, __version__, config, i18n, transfer, updater
from .discovery import Discovery
from .dragwatch import DragWatcher
from .net import local_ips
from .remote import RemoteHost, RemoteViewer
from .share import Client, Server
from .winapi import is_admin, run_elevated

_HOST_RE = re.compile(r"^[A-Za-z0-9.\-:_]{1,253}$")


class Controller:
    def __init__(self):
        self.cfg = config.load()
        i18n.set_lang(self.cfg["lang"])
        self.subs = []
        self.lock = threading.RLock()
        self.server = self.client = self.rhost = None
        self.clients, self.sessions, self.logs, self.received = [], [], [], []
        self.active_name, self.cstate = None, "disconnected"
        self.viewers = {}
        self.approvals = {}
        self.transfers = {}
        self.tokens = {}                    # server pending token -> transfer id
        self.update = {"code": "", "kw": {}, "pct": None, "version": None}
        self.update_info = None
        self.attention = lambda: None       # set by the launcher: bring window to front
        self.titlebar = lambda dark: None   # set by the launcher
        self.overlay_cb = lambda action, edge=None: None   # show/hide the edge drop panel (launcher)
        self.outbox = os.path.join(tempfile.gettempdir(), "FramioOutbox")
        self._clean_outbox()
        self.disc = Discovery(self._disc_info)
        self.disc.start()
        if self.cfg["share_running"]:
            self.api_share_toggle(force_start=True)
        if self.cfg["remote_enabled"]:
            self._start_remote()
        self.dw = DragWatcher(self._drag_candidates, self._overlay_show, self._overlay_hide)
        self.dw.start()
        threading.Thread(target=self._tick, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()

    # ------------------------------------------------------------ events out
    def emit(self, kind, **kw):
        msg = json.dumps({"k": kind, **kw}, default=str)
        for q in list(self.subs):
            q.put(msg)

    def changed(self):
        self.emit("state", state=self.state())

    def toast(self, key, kind="info", **kw):
        self.emit("toast", key=key, kind=kind, kw=kw)

    def logcb(self, code, **kw):
        item = {"t": time.strftime("%H:%M:%S"), "code": code, "kw": kw}
        self.logs.append(item)
        del self.logs[:-150]
        self.emit("log", item=item)

    def ev(self, kind, **kw):
        if kind == "clients":
            self.clients = kw["clients"]
        elif kind == "active":
            self.active_name = kw["name"]
        elif kind == "state":
            self.cstate = kw["state"]
        elif kind == "rsessions":
            self.sessions = kw["sessions"]
        elif kind == "files" and kw.get("mode") == "drag":
            self._begin_drag(kw["paths"])
            return
        elif kind == "files":
            who = "host" if kw["who"] == "host" else kw["who"]
            for p in kw["paths"]:
                self.received.insert(0, {"t": time.strftime("%H:%M"), "who": who,
                                         "name": os.path.basename(p), "path": p})
            del self.received[30:]
            self.toast("got_files", "ok", n=len(kw["paths"]), who=who)
        elif kind == "pulled":
            tid = self.tokens.pop(kw["token"], None)
            if tid in self.transfers:
                self.transfers[tid].update(status="done", pct=100)
        self.changed()

    # ------------------------------------------------------------ state
    def _disc_info(self):
        return {"id": self.cfg["device_id"], "name": self.cfg["device_name"],
                "share_port": int(self.cfg["port"]) if self.server else None,
                "remote_port": int(self.cfg["remote_port"]) if self.rhost else None}

    def peers(self):
        return [{"id": p["id"], "name": p["name"], "ip": p["ip"],
                 "share": bool(p.get("share_port")), "remote": bool(p.get("remote_port"))}
                for p in self.disc.list(self.cfg["device_id"])]

    def status(self):
        if self.server:
            if self.active_name:
                return "active"
            return "connected" if self.clients else "waiting"
        if self.client:
            return self.cstate            # connected / connecting / disconnected
        return "stopped"

    def state(self):
        c = self.cfg
        return {
            "version": __version__, "lang": c["lang"], "theme": c["theme"],
            "mode": c["mode"], "side": c["side"], "key": c["key"], "host": c["host"],
            "clip_text": c["clip_text"], "clip_files": c["clip_files"],
            "running": bool(self.server or self.client), "status": self.status(),
            "active": self.active_name, "clients": self.clients, "ips": local_ips(),
            "device": {"id": c["device_id"], "name": c["device_name"]},
            "remote": {"enabled": bool(self.rhost), "password": c["remote_password"],
                       "approval": c["remote_approval"], "sessions": self.sessions},
            "peers": self.peers(), "received": self.received,
            "transfers": list(self.transfers.values())[-6:],
            "update": self.update, "auto_update": c["auto_update"],
            "autostart": config.autostart_enabled(), "admin": is_admin(),
            "frozen": bool(getattr(sys, "frozen", False)),
            "viewers": list(self.viewers.keys()),
        }

    def logs_snapshot(self):
        return self.logs[-80:]

    def _tick(self):
        sig = None
        while True:
            time.sleep(2)
            s = [(p["id"], p["ip"], p["share"], p["remote"]) for p in self.peers()]
            if s != sig:
                sig = s
                self.changed()

    # ------------------------------------------------------------ edge drop panel
    def _drag_candidates(self):
        if self.server:
            if self.server.active:
                return []
            return [{"edge": c["side"], "name": c["name"], "key": c["side"]} for c in self.clients]
        if self.client and self.client.connected:
            edge = "left" if self.cfg["side"] == "right" else "right"   # the edge that faces the host
            return [{"edge": edge, "name": "host", "key": "host"}]
        return []

    def _overlay_show(self, c):
        self.emit("overlay", name=c["name"], key=c["key"], edge=c["edge"])
        self.overlay_cb("show", c["edge"])

    def _overlay_hide(self):
        self.emit("overlay_hide")
        self.overlay_cb("hide")

    def _begin_drag(self, paths):
        """Files arrived in 'drag' mode: continue the drag on this PC so the user drops them anywhere."""
        from . import dragsource
        from .winapi import clip_set_files
        if self.server and self.server.active:          # we were controlling the sender: take the PC back
            self.server._leave()
        if dragsource.start_drag(paths):
            self.toast("drag_hint", "ok")
        else:                                            # no pythonnet: fall back to the clipboard
            clip_set_files(paths)
            self.toast("drag_fallback", "warn")

    # ------------------------------------------------------------ share
    def _start_share(self):
        c = self.cfg
        if len(c["key"]) < 4:
            return "need_key", {}
        if c["mode"] == "client" and not c["host"]:
            return "need_host", {}
        try:
            port = int(c["port"])
            if c["mode"] == "host":
                s = Server(c["key"], port, self.logcb, self.ev, c["clip_text"], c["clip_files"])
                s.start()
                self.server = s
            else:
                cl = Client(c["host"], port, c["key"], c["side"], self.logcb, self.ev,
                            c["clip_text"], c["clip_files"])
                cl.start()
                self.client = cl
        except (OSError, ValueError) as e:
            return "err_start", {"msg": str(e)}
        c["share_running"] = True
        config.save(c)
        self.logcb("started")
        return None, {}

    def _stop_share(self):
        for o in (self.server, self.client):
            if o:
                o.stop()
        self.server = self.client = None
        self.clients, self.active_name, self.cstate = [], None, "disconnected"
        self.cfg["share_running"] = False
        config.save(self.cfg)
        self.logcb("stopped")

    def api_share_toggle(self, force_start=False):
        with self.lock:
            if (self.server or self.client) and not force_start:
                self._stop_share()
            else:
                err, kw = self._start_share()
                if err:
                    self.toast(err, "danger", **kw)
            self.changed()
        return True

    def api_new_key(self):
        self.cfg["key"] = secrets.token_hex(4).upper()
        if self.server:
            self.server.key = self.cfg["key"]
        config.save(self.cfg)
        self.changed()
        return True

    def api_set_config(self, patch):
        c, restart_client = self.cfg, False
        with self.lock:
            for k, v in (patch or {}).items():
                if k == "lang" and v in i18n.S:
                    c[k] = v
                    i18n.set_lang(v)
                elif k == "theme" and v in ("dark", "light", "auto"):
                    c[k] = v
                elif k == "mode" and v in ("host", "client") and v != c["mode"]:
                    if self.server or self.client:
                        self._stop_share()
                    c[k] = v
                elif k == "side" and v in ("left", "right") and v != c["side"]:
                    c[k] = v
                    restart_client = bool(self.client)
                elif k == "key" and isinstance(v, str) and len(v) <= 64:
                    c[k] = v.strip()
                    if self.server:
                        self.server.key = c[k]
                elif k == "host" and isinstance(v, str) and (v == "" or _HOST_RE.match(v.strip())):
                    c[k] = v.strip()
                elif k in ("clip_text", "clip_files", "remote_approval", "auto_update") and isinstance(v, bool):
                    c[k] = v
                    for o in (self.server, self.client):
                        if o and k in ("clip_text", "clip_files"):
                            setattr(o, k, v)
            config.save(c)
            if restart_client:
                self._stop_share()
                self._start_share()
            self.changed()
        return True

    # ------------------------------------------------------------ remote desktop
    def _start_remote(self):
        if self.rhost:
            return
        try:
            r = RemoteHost(self.cfg["remote_password"], int(self.cfg["remote_port"]), self.logcb,
                           self.ev, approve=self._approve, name=self.cfg["device_name"])
            r.start()
            self.rhost = r
        except OSError as e:
            self.toast("err_start", "danger", msg=str(e))

    def _approve(self, name, ip):
        if not self.cfg["remote_approval"]:
            return True
        aid = secrets.token_hex(4)
        evt, holder = threading.Event(), {}
        self.approvals[aid] = (evt, holder)
        self.attention()
        self.emit("approve", id=aid, name=name, ip=ip)
        evt.wait(35)
        self.approvals.pop(aid, None)
        return holder.get("ok", False)

    def api_approve(self, aid, ok):
        ent = self.approvals.get(aid)
        if ent:
            ent[1]["ok"] = bool(ok)
            ent[0].set()
        return True

    def api_remote_allow(self, on):
        with self.lock:
            self.cfg["remote_enabled"] = bool(on)
            config.save(self.cfg)
            if on:
                self._start_remote()
            elif self.rhost:
                self.rhost.stop()
                self.rhost, self.sessions = None, []
            self.changed()
        return True

    def api_new_remote_pw(self):
        pw = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        self.cfg["remote_password"] = pw
        if self.rhost:
            self.rhost.password = pw
        config.save(self.cfg)
        self.changed()
        return True

    def api_disconnect_session(self, sid):
        if self.rhost:
            self.rhost.disconnect(sid)
        return True

    def api_connect(self, partner, pw):
        partner, pw = (partner or "").strip(), (pw or "").strip()
        if not partner or not pw:
            return {"error": "fill_fields"}
        digits = partner.replace(" ", "")
        host, port, found = partner, int(self.cfg["remote_port"]), False
        for p in self.disc.list(self.cfg["device_id"]):
            if digits == str(p["id"]) or partner == p["ip"]:
                host, port, found = p["ip"], p.get("remote_port") or port, True
        if digits.isdigit() and len(digits) == 9 and not found:
            return {"error": "id_not_found"}
        if not _HOST_RE.match(host):
            return {"error": "bad_host"}
        vid = secrets.token_hex(4)
        v = RemoteViewer(host, port, pw, on_close=lambda: self._viewer_closed(vid))
        try:
            v.connect(self.cfg["device_name"].replace(" ", "_"))
        except Exception as e:
            return {"error": "r_failed", "msg": str(e)}
        self.viewers[vid] = v
        self.changed()
        return {"vid": vid, "host": host}

    def _viewer_closed(self, vid):
        if self.viewers.pop(vid, None) is not None:
            self.emit("viewer_closed", vid=vid)
            self.changed()

    def api_rclose(self, vid):
        v = self.viewers.get(vid)
        if v:
            v.close()
        return True

    def rinput(self, vid, text):
        v = self.viewers.get(vid)
        if v:
            for line in text.split("\n")[:200]:
                if line:
                    v.send_input(line)

    # ------------------------------------------------------------ files
    def _clean_outbox(self):
        try:
            os.makedirs(self.outbox, exist_ok=True)
            now = time.time()
            for d in os.listdir(self.outbox):
                p = os.path.join(self.outbox, d)
                if now - os.path.getmtime(p) > 3600:
                    shutil.rmtree(p, ignore_errors=True)
        except OSError:
            pass

    def outbox_path(self, batch, rel):
        if not re.fullmatch(r"[a-f0-9]{8,32}", batch or ""):
            return None
        parts = [p for p in re.split(r"[\\/]+", rel or "") if p not in ("", ".", "..")]
        parts = [re.sub(r'[<>:"|?*\x00-\x1f]', "_", p).strip(" .") or "_" for p in parts]
        if not parts:
            return None
        return os.path.join(self.outbox, batch, *parts)

    def api_send(self, batch, target, names, mode="file"):
        mode = "drag" if mode == "drag" else "file"
        if not re.fullmatch(r"[a-f0-9]{8,32}", batch or ""):
            return {"error": "bad_batch"}
        base = os.path.join(self.outbox, batch)
        tops = sorted({n for n in os.listdir(base)}) if os.path.isdir(base) else []
        paths = [os.path.join(base, n) for n in tops]
        if not paths:
            return {"error": "nothing"}
        label = ", ".join(tops[:2]) + (f" +{len(tops) - 2}" if len(tops) > 2 else "")
        tid = secrets.token_hex(4)
        total = max(1, transfer.total_size(paths))
        self.transfers[tid] = {"id": tid, "name": label, "pct": 0, "status": "sending", "target": target}
        if self.server:
            conn = self.server.clients.get(target)
            if not conn:
                return {"error": "no_target"}
            tok = self.server.send_files_to(conn, paths, mode)
            self.tokens[tok] = tid
            self.transfers[tid]["status"] = "queued"
            if mode == "drag":
                self.server.enter_here(conn)          # keyboard/mouse follow the files to the other PC
            self.changed()
            return {"ok": True}
        if self.client and self.client.connected:
            def work():
                try:
                    def prog(n):
                        pct = int(n * 100 / total)
                        if pct != self.transfers[tid]["pct"]:
                            self.transfers[tid]["pct"] = pct
                            self.changed()
                    self.client.push_files(paths, mode, prog)
                    self.transfers[tid].update(status="done", pct=100)
                except Exception as e:
                    self.transfers[tid].update(status="error")
                    self.toast("log_error", "danger", msg=str(e))
                self.changed()
            threading.Thread(target=work, daemon=True).start()
            return {"ok": True}
        return {"error": "no_target"}

    def api_open_downloads(self):
        os.startfile(transfer.downloads_dir())
        return True

    def api_reveal(self, idx):
        try:
            p = self.received[int(idx)]["path"]
            if os.path.exists(p):
                subprocess.Popen(["explorer", "/select,", os.path.normpath(p)])
        except (ValueError, IndexError):
            pass
        return True

    # ------------------------------------------------------------ system
    def api_set_autostart(self, on):
        try:
            config.set_autostart(bool(on))
        except Exception as e:
            self.toast("log_error", "danger", msg=str(e))
        self.changed()
        return True

    def api_install_here(self):
        try:
            dst = config.install_here()
            self.toast("installed", "ok", path=dst)
        except Exception as e:
            self.toast("log_error", "danger", msg=str(e))
        self.changed()
        return True

    def api_firewall(self):
        run_elevated("cmd.exe", config.firewall_command(int(self.cfg["port"]), int(self.cfg["remote_port"]),
                                                          DISCOVERY_PORT))
        return True

    def api_restart_admin(self):
        exe = sys.executable
        args = "" if getattr(sys, "frozen", False) else " ".join(f'"{a}"' for a in sys.argv)
        if run_elevated(exe, args):
            self.quit()
        return True

    def api_set_titlebar(self, dark):
        self.titlebar(bool(dark))
        return True

    # ------------------------------------------------------------ updates
    def _set_update(self, code, pct=None, **kw):
        self.update = {"code": code, "kw": kw, "pct": pct, "version": (self.update_info or {}).get("version")}
        self.changed()

    def api_check_updates(self, manual=True):
        threading.Thread(target=self._check, args=(manual,), daemon=True).start()
        return True

    def _check(self, manual):
        try:
            if GITHUB_REPO.startswith("OWNER/"):
                return self._set_update("update_norepo")
            self._set_update("checking")
            info = updater.check()
            if not info:
                return self._set_update("up_to_date")
            self.update_info = info
            self._set_update("update_found", v=info["version"])
            if manual or self.cfg["auto_update"]:
                self._install(info)
        except Exception as e:
            self._set_update("update_err", msg=str(e))

    def busy(self):
        return bool(self.server and self.server.active) or bool(self.sessions) or bool(self.viewers)

    def _install(self, info):
        while self.busy():
            time.sleep(30)
        self._set_update("updating", 0, v=info["version"])
        path = updater.download(info, lambda f: self._set_update("updating", int(f * 100), v=info["version"]))
        updater.apply_and_restart(path)
        time.sleep(0.5)
        self.quit()

    def _update_loop(self):
        time.sleep(15)
        while True:
            if self.cfg["auto_update"] and getattr(sys, "frozen", False):
                self._check(False)
            time.sleep(6 * 3600)

    # ------------------------------------------------------------ lifecycle
    def call(self, name, args):
        fn = getattr(self, "api_" + name, None)
        if not fn or name.startswith("_"):
            raise KeyError(name)
        return fn(*args)

    def quit(self):
        try:
            config.save(self.cfg)
        except Exception:
            pass
        for o in (self.server, self.client, self.rhost, self.disc):
            try:
                o and o.stop()
            except Exception:
                pass
        os._exit(0)
