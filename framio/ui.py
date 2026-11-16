"""Framio Connect main window."""
import ctypes
import io
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog

from PIL import Image, ImageTk

from . import (APP_NAME, DISCOVERY_PORT, GITHUB_REPO, REMOTE_PORT, SHARE_PORT, __version__,
               brand, config, i18n, transfer, updater)
from .discovery import Discovery
from .i18n import T
from .net import local_ips
from .remote import RemoteHost, RemoteViewer
from .share import Client, Server
from .widgets import F, RButton, Segmented, Toggle, make_entry, modal, rr
from .winapi import EXTENDED_VKS, is_admin, run_elevated, set_dark_titlebar

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAVE_DND = True
except Exception:
    HAVE_DND = False


class App:
    def __init__(self, autostart=False):
        self.cfg = config.load()
        i18n.set_lang(self.cfg["lang"])
        self.root = TkinterDnD.Tk() if HAVE_DND else tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("1060x800")
        self.root.minsize(960, 700)
        self._icon = ImageTk.PhotoImage(brand.logo_image(64))
        self.root.iconphoto(True, self._icon)
        self.q = queue.Queue()
        self.server = self.client = self.rhost = None
        self.clients, self.sessions, self.logs, self.received = [], [], [], []
        self.active_name, self.cstate = None, "disconnected"
        self.cur, self.w, self.c = "share", {}, brand.DARK
        self.update_info, self.upd_text = None, ""
        self.viewers = []
        self.f_target = tk.StringVar()
        self.partner_var, self.ppw_var = tk.StringVar(), tk.StringVar()
        self.key_var = tk.StringVar(value=self.cfg["key"])
        self.host_var = tk.StringVar(value=self.cfg["host"])
        self.mode_var = tk.StringVar(value=self.cfg["mode"])
        self.side_var = tk.StringVar(value=self.cfg["side"])
        self.clip_text_var = tk.BooleanVar(value=self.cfg["clip_text"])
        self.clip_files_var = tk.BooleanVar(value=self.cfg["clip_files"])
        self.lang_var = tk.StringVar(value=self.cfg["lang"])
        self.theme_var = tk.StringVar(value=self.cfg["theme"])
        self.allow_var = tk.BooleanVar(value=self.cfg["remote_enabled"])
        self.approval_var = tk.BooleanVar(value=self.cfg["remote_approval"])
        self.autostart_var = tk.BooleanVar(value=config.autostart_enabled())
        self.autoupd_var = tk.BooleanVar(value=self.cfg["auto_update"])
        self.pw_shown = False
        self.build()
        self.disc = Discovery(self.disc_info)
        self.disc.start()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.after(100, self.pump)
        self.root.after(2000, self.tick)
        if self.cfg["share_running"]:
            self.start_share(silent=True)
        if self.cfg["remote_enabled"]:
            self.start_remote()
        self.root.after(15000, self.auto_update_loop)
        if autostart:
            self.root.iconify()
        shot = os.environ.get("FRAMIO_SHOT")
        if shot:
            self.root.after(1500, lambda: self._shot(shot))

    # ================================================================ services
    def ev(self, kind, **kw):
        self.q.put(("ev", kind, kw))

    def logcb(self, code, **kw):
        self.q.put(("log", code, kw))

    def disc_info(self):
        return {"id": self.cfg["device_id"], "name": self.cfg["device_name"],
                "share_port": int(self.cfg["port"]) if self.server else None,
                "remote_port": int(self.cfg["remote_port"]) if self.rhost else None}

    def save(self):
        self.cfg.update(key=self.key_var.get().strip(), host=self.host_var.get().strip(),
                        mode=self.mode_var.get(), side=self.side_var.get(),
                        clip_text=self.clip_text_var.get(), clip_files=self.clip_files_var.get(),
                        remote_approval=self.approval_var.get(), auto_update=self.autoupd_var.get())
        config.save(self.cfg)

    def start_share(self, silent=False):
        self.save()
        k = self.cfg["key"]
        if len(k) < 4:
            return self.notice(T("need_key"), "danger")
        if self.cfg["mode"] == "client" and not self.cfg["host"]:
            return self.notice(T("need_host"), "danger")
        try:
            port = int(self.cfg["port"])
            if self.cfg["mode"] == "host":
                s = Server(k, port, self.logcb, self.ev, self.cfg["clip_text"], self.cfg["clip_files"])
                s.start()
                self.server = s
            else:
                c = Client(self.cfg["host"], port, k, self.cfg["side"], self.logcb, self.ev,
                           self.cfg["clip_text"], self.cfg["clip_files"])
                c.start()
                self.client = c
        except (OSError, ValueError) as e:
            return self.notice(T("err_start", msg=e), "danger")
        self.cfg["share_running"] = True
        config.save(self.cfg)
        self.logcb("started")
        self.refresh_all()

    def stop_share(self):
        for o in (self.server, self.client):
            if o:
                o.stop()
        self.server = self.client = None
        self.clients, self.active_name, self.cstate = [], None, "disconnected"
        self.cfg["share_running"] = False
        config.save(self.cfg)
        self.logcb("stopped")
        self.refresh_all()

    def start_remote(self):
        if self.rhost:
            return
        try:
            r = RemoteHost(self.cfg["remote_password"], int(self.cfg["remote_port"]), self.logcb, self.ev,
                           approve=self._approve, name=self.cfg["device_name"])
            r.start()
            self.rhost = r
        except OSError as e:
            self.allow_var.set(False)
            self.notice(T("err_start", msg=e), "danger")

    def stop_remote(self):
        if self.rhost:
            self.rhost.stop()
        self.rhost, self.sessions = None, []

    def _approve(self, name, ip):
        if not self.cfg["remote_approval"]:
            return True
        evt, holder = threading.Event(), {}
        self.q.put(("approve", name, ip, evt, holder))
        evt.wait(35)
        return holder.get("ok", False)

    # ================================================================ pump
    def pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                try:
                    self.handle(item)
                except tk.TclError:
                    pass
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def tick(self):
        try:
            self.refresh_nearby()
        except tk.TclError:
            pass
        self.root.after(2000, self.tick)

    def handle(self, it):
        k = it[0]
        if k == "log":
            _, code, kw = it
            kw = dict(kw)
            if "side" in kw:
                kw["side"] = T(kw["side"])
            self.add_log(T("log_" + code, **kw) if ("log_" + code) in i18n.S["en"] else code)
        elif k == "ev":
            _, kind, kw = it
            if kind == "clients":
                self.clients = kw["clients"]
                self.render_targets()
            elif kind == "active":
                self.active_name = kw["name"]
            elif kind == "state":
                self.cstate = kw["state"]
                self.render_targets()
            elif kind == "files":
                n = len(kw["paths"])
                who = T("host_word") if kw["who"] == "host" else kw["who"]
                for pth in kw["paths"]:
                    self.received.insert(0, (time.strftime("%H:%M"), who, pth))
                del self.received[30:]
                self.notice(T("got_files", n=n, who=who), "ok")
                self.render_received()
            elif kind == "rsessions":
                self.sessions = kw["sessions"]
                self.render_sessions()
            self.refresh_share()
        elif k == "approve":
            _, name, ip, evt, holder = it
            self.root.deiconify()
            holder["ok"] = modal(self.root, self.c, T("approve_title"), T("approve_msg", name=name, ip=ip),
                                 T("allow"), T("deny"))
            evt.set()
        elif k == "notice":
            self.notice(it[1], it[2])
        elif k == "viewer":
            ViewerWindow(self, it[1], it[2])
        elif k == "upd":
            self.upd_text = it[1]
            if "upd" in self.w:
                self.w["upd"].config(text=self.upd_text)
        elif k == "quit":
            self.quit()

    def add_log(self, text):
        line = time.strftime("%H:%M:%S  ") + text
        self.logs.append(line)
        del self.logs[:-200]
        t = self.w.get("log")
        if t:
            t.config(state="normal")
            t.insert("end", line + "\n")
            t.see("end")
            t.config(state="disabled")

    def notice(self, text, kind="muted"):
        lb = self.w.get("notice")
        if not lb:
            return
        lb.config(text=text, fg=self.c.get(kind, self.c["muted"]))
        tag = object()
        self._ntag = tag
        self.root.after(7000, lambda: lb.config(text="") if getattr(self, "_ntag", None) is tag else None)

    # ================================================================ build
    def apply_theme(self):
        self.c, name = brand.palette(self.cfg["theme"])
        self.dark = name == "dark"

    def build(self):
        self.apply_theme()
        c = self.c
        for ch in self.root.winfo_children():
            ch.destroy()
        self.w = {}
        self.root.configure(bg=c["bg"])
        self.root.update_idletasks()
        set_dark_titlebar(ctypes.windll.user32.GetParent(self.root.winfo_id()), self.dark)

        side = tk.Frame(self.root, bg=c["side"], width=220)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(self.root, bg=c["border"], width=1).pack(side="left", fill="y")

        self._logo = ImageTk.PhotoImage(brand.logo_image(44))
        top = tk.Frame(side, bg=c["side"])
        top.pack(fill="x", padx=20, pady=(24, 26))
        tk.Label(top, image=self._logo, bg=c["side"]).pack(side="left")
        tt = tk.Frame(top, bg=c["side"])
        tt.pack(side="left", padx=12)
        tk.Label(tt, text="Framio", font=F(16, "bold"), bg=c["side"], fg=c["text"]).pack(anchor="w")
        tk.Label(tt, text="Connect", font=F(10), bg=c["side"], fg=c["accent"]).pack(anchor="w")

        self.nav = {}
        for key, tkey in (("share", "nav_share"), ("files", "nav_files"),
                          ("remote", "nav_remote"), ("settings", "nav_settings")):
            row = tk.Frame(side, bg=c["side"], cursor="hand2")
            row.pack(fill="x", padx=12, pady=2)
            bar = tk.Frame(row, bg=c["side"], width=3)
            bar.pack(side="left", fill="y")
            lb = tk.Label(row, text=T(tkey), font=F(11, "bold"), bg=c["side"], fg=c["muted"],
                          anchor="w", padx=14, pady=10)
            lb.pack(side="left", fill="x", expand=True)
            for wdg in (row, lb):
                wdg.bind("<Button-1>", lambda e, k=key: self.show(k))
            self.nav[key] = (row, bar, lb)

        bottom = tk.Frame(side, bg=c["side"])
        bottom.pack(side="bottom", fill="x", padx=20, pady=18)
        tk.Label(bottom, text=T("tagline"), font=F(9), bg=c["side"], fg=c["muted"],
                 wraplength=170, justify="left").pack(anchor="w")
        tk.Label(bottom, text=f"v{__version__}", font=F(9), bg=c["side"], fg=c["muted"]).pack(
            anchor="w", pady=(6, 0))

        main = tk.Frame(self.root, bg=c["bg"])
        main.pack(side="left", fill="both", expand=True)
        foot = tk.Frame(main, bg=c["bg"], height=30)
        foot.pack(side="bottom", fill="x")
        self.w["notice"] = tk.Label(foot, text="", font=F(10), bg=c["bg"], fg=c["muted"], anchor="w")
        self.w["notice"].pack(fill="x", padx=28)
        self.pages = {}
        body = tk.Frame(main, bg=c["bg"])
        body.pack(fill="both", expand=True)
        for key, fn in (("share", self.page_share), ("files", self.page_files),
                        ("remote", self.page_remote), ("settings", self.page_settings)):
            pg = tk.Frame(body, bg=c["bg"])
            pg.place(relx=0, rely=0, relwidth=1, relheight=1)
            fn(pg)
            self.pages[key] = pg
        self.show(self.cur)
        for line in self.logs[-200:]:
            t = self.w["log"]
            t.config(state="normal")
            t.insert("end", line + "\n")
            t.config(state="disabled")
        self.refresh_all()

    def show(self, key):
        self.cur = key
        c = self.c
        for k, (row, bar, lb) in self.nav.items():
            on = k == key
            for wdg in (row, lb):
                wdg.config(bg=c["card2"] if on else c["side"])
            bar.config(bg=c["accent"] if on else c["side"])
            lb.config(fg=c["text"] if on else c["muted"])
        self.pages[key].tkraise()

    # ---------------------------------------------------------- building blocks
    def header(self, p, title, sub):
        c = self.c
        h = tk.Frame(p, bg=c["bg"])
        h.pack(fill="x", padx=28, pady=(26, 16))
        tk.Label(h, text=title, font=F(20, "bold"), bg=c["bg"], fg=c["text"]).pack(anchor="w")
        tk.Label(h, text=sub, font=F(10), bg=c["bg"], fg=c["muted"], wraplength=680,
                 justify="left").pack(anchor="w", pady=(4, 0))

    def card(self, p, title=None, **pk):
        c = self.c
        f = tk.Frame(p, bg=c["card"], highlightthickness=1, highlightbackground=c["border"])
        inner = tk.Frame(f, bg=c["card"])
        inner.pack(fill="both", expand=True, padx=20, pady=16)
        if title:
            tk.Label(inner, text=title, font=F(11, "bold"), bg=c["card"], fg=c["text"]).pack(
                anchor="w", pady=(0, 10))
        f.inner = inner
        return f

    def row(self, p, label):
        c = self.c
        r = tk.Frame(p, bg=c["card"])
        r.pack(fill="x", pady=5)
        tk.Label(r, text=label, font=F(10), bg=c["card"], fg=c["muted"], width=26, anchor="w").pack(side="left")
        right = tk.Frame(r, bg=c["card"])
        right.pack(side="left")
        return right

    def toggle_row(self, p, label, var, cmd=None):
        c = self.c
        r = tk.Frame(p, bg=c["card"])
        r.pack(fill="x", pady=5)
        Toggle(r, c, var, cmd).pack(side="left")
        tk.Label(r, text=label, font=F(10), bg=c["card"], fg=c["text"]).pack(side="left", padx=12)

    def copy(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.notice(T("copied"), "ok")

    # ================================================================ SHARE page
    def page_share(self, p):
        c = self.c
        self.header(p, T("share_title"), T("share_sub"))
        card = self.card(p)
        card.pack(fill="x", padx=28, pady=(0, 14))
        r = self.row(card.inner, T("role"))
        Segmented(r, c, [("host", T("role_host")), ("client", T("role_client"))],
                  self.mode_var, self.on_role).pack()
        self.w["fields"] = tk.Frame(card.inner, bg=c["card"])
        self.w["fields"].pack(fill="x")
        self.render_share_fields()
        act = tk.Frame(card.inner, bg=c["card"])
        act.pack(fill="x", pady=(12, 0))
        self.w["start_btn"] = RButton(act, c, T("start"), self.toggle_share, width=130)
        self.w["start_btn"].pack(side="left")
        dot = tk.Canvas(act, width=14, height=14, bg=c["card"], highlightthickness=0)
        dot.pack(side="left", padx=(18, 6))
        self.w["dot"] = dot
        self.w["status"] = tk.Label(act, text="", font=F(10, "bold"), bg=c["card"], fg=c["muted"])
        self.w["status"].pack(side="left")

        lay = self.card(p, T("layout"))
        lay.pack(fill="x", padx=28, pady=(0, 14))
        cv = tk.Canvas(lay.inner, width=580, height=96, bg=c["card"], highlightthickness=0)
        cv.pack(anchor="w")
        self.w["layout"] = cv
        tk.Label(lay.inner, text=T("hint_switch"), font=F(9), bg=c["card"], fg=c["muted"]).pack(
            anchor="w", pady=(6, 0))

        lg = self.card(p, T("log"))
        lg.pack(fill="both", expand=True, padx=28, pady=(0, 8))
        t = tk.Text(lg.inner, height=5, bg=c["input"], fg=c["text"], relief="flat", bd=0,
                    font=("Consolas", 9), state="disabled", highlightthickness=0, padx=8, pady=6)
        t.pack(fill="both", expand=True)
        self.w["log"] = t

    def render_share_fields(self):
        c, f = self.c, self.w["fields"]
        for ch in f.winfo_children():
            ch.destroy()
        host = self.mode_var.get() == "host"
        if host:
            r = self.row(f, T("key"))
            make_entry(r, c, self.key_var, 18, mono=True).pack(side="left")
            RButton(r, c, T("copy"), lambda: self.copy(self.key_var.get()), "ghost", height=32).pack(side="left", padx=8)
            RButton(r, c, T("new_key"), self.new_key, "ghost", height=32).pack(side="left")
            r = self.row(f, T("my_ips"))
            tk.Label(r, text="   ".join(local_ips()) or "-", font=("Consolas", 11), bg=c["card"],
                     fg=c["accent"]).pack(side="left")
        else:
            r = self.row(f, T("host_ip"))
            make_entry(r, c, self.host_var, 18, mono=True).pack(side="left")
            RButton(r, c, T("find_host"), self.find_host, "ghost", height=32).pack(side="left", padx=8)
            r = self.row(f, T("key"))
            make_entry(r, c, self.key_var, 18, mono=True).pack(side="left")
            r = self.row(f, T("my_side"))
            Segmented(r, c, [("left", T("left")), ("right", T("right"))], self.side_var,
                      self.on_side).pack()
        self.toggle_row(f, T("clip_text"), self.clip_text_var, self.save)
        self.toggle_row(f, T("clip_files"), self.clip_files_var, self.save)

    def on_role(self):
        if self.server or self.client:
            self.stop_share()
        self.save()
        self.render_share_fields()
        self.refresh_all()

    def on_side(self):
        if self.client:
            self.stop_share()
            self.start_share()
        else:
            self.save()
            self.refresh_share()

    def new_key(self):
        self.key_var.set(secrets.token_hex(4).upper())
        self.save()

    def find_host(self):
        peers = [p for p in self.disc.list(self.cfg["device_id"]) if p.get("share_port")]
        if not peers:
            return self.notice(T("no_hosts"), "warn")
        m = tk.Menu(self.root, tearoff=0, bg=self.c["card"], fg=self.c["text"],
                    activebackground=self.c["accent"], activeforeground=self.c["accent_fg"])
        for pr in peers:
            m.add_command(label=f"{pr['name']}   {pr['ip']}", command=lambda ip=pr["ip"]: self.host_var.set(ip))
        x, y = self.root.winfo_pointerxy()
        m.tk_popup(x, y)

    def toggle_share(self):
        if self.server or self.client:
            self.stop_share()
        else:
            self.start_share()

    def share_status(self):
        c = self.c
        if self.server:
            if self.active_name:
                return T("st_active", name=self.active_name), c["accent"]
            if self.clients:
                return T("st_connected"), c["ok"]
            return T("st_waiting"), c["warn"]
        if self.client:
            return {"connected": (T("st_connected"), c["ok"]),
                    "connecting": (T("st_connecting"), c["warn"])}.get(
                self.cstate, (T("st_disconnected"), c["danger"]))
        return T("st_stopped"), c["muted"]

    def refresh_all(self):
        self.refresh_share()
        self.render_targets()
        self.render_sessions()
        self.render_received()
        self.refresh_nearby()

    def refresh_share(self):
        w = self.w
        if "dot" not in w:
            return
        running = bool(self.server or self.client)
        w["start_btn"].set_text(T("stop") if running else T("start"))
        text, col = self.share_status()
        w["status"].config(text=text, fg=col)
        w["dot"].delete("all")
        w["dot"].create_oval(2, 2, 12, 12, fill=col, outline="")
        self.draw_layout()

    def draw_layout(self):
        cv = self.w.get("layout")
        if not cv:
            return
        c = self.c
        cv.delete("all")
        bw, bh, gap, y = 160, 70, 40, 10
        host = self.mode_var.get() == "host"

        def box(x, title, sub, hl=False, dim=False):
            rr(cv, x, y, x + bw, y + bh, 12, fill=c["card2"], outline=c["accent"] if hl else c["border"],
               width=2 if hl else 1)
            cv.create_text(x + bw / 2, y + 28, text=title, font=F(11, "bold"),
                           fill=c["muted"] if dim else c["text"])
            cv.create_text(x + bw / 2, y + 48, text=sub, font=F(9), fill=c["muted"])

        if host:
            by = {cc["side"]: cc for cc in self.clients}
            xs = [0, bw + gap, 2 * (bw + gap)]
            for x, sd in ((xs[0], "left"), (xs[2], "right")):
                cc = by.get(sd)
                if cc:
                    box(x, cc["name"], cc["ip"], hl=cc["name"] == self.active_name)
                else:
                    box(x, T("waiting_pc"), T(sd), dim=True)
            box(xs[1], T("this_pc"), T("host_label"), hl=self.active_name is None and bool(self.server))
        else:
            left = self.side_var.get() == "left"
            a, b = ((0, bw + gap) if left else (bw + gap, 0))
            ok = self.cstate == "connected"
            box(a, T("this_pc"), T("role_client"), hl=ok)
            box(b, T("host_label"), self.host_var.get() or "-", dim=not ok)
        cv.create_line(0, 0, 0, 0)

    # ================================================================ FILES page
    def page_files(self, p):
        c = self.c
        self.header(p, T("files_title"), T("files_sub"))
        card = self.card(p)
        card.pack(fill="x", padx=28, pady=(0, 14))
        r = self.row(card.inner, T("send_to"))
        self.w["targets"] = r
        cv = tk.Canvas(card.inner, height=170, bg=c["card"], highlightthickness=0, cursor="hand2")
        cv.pack(fill="x", pady=(12, 0))
        self.w["drop"] = cv
        cv.bind("<Configure>", lambda e: self.draw_drop())
        cv.bind("<Button-1>", lambda e: self.browse())
        if HAVE_DND:
            cv.drop_target_register(DND_FILES)
            cv.dnd_bind("<<Drop>>", self.on_drop)
            cv.dnd_bind("<<DropEnter>>", lambda e: self.draw_drop(True))
            cv.dnd_bind("<<DropLeave>>", lambda e: self.draw_drop(False))
        rc = self.card(p, T("received"))
        rc.pack(fill="both", expand=True, padx=28, pady=(0, 8))
        top = tk.Frame(rc.inner, bg=c["card"])
        top.pack(fill="x")
        RButton(top, c, T("open_folder"), lambda: os.startfile(transfer.downloads_dir()), "ghost", height=32).pack(anchor="w")
        self.w["recv"] = tk.Frame(rc.inner, bg=c["card"])
        self.w["recv"].pack(fill="both", expand=True, pady=(10, 0))

    def draw_drop(self, hot=False):
        cv = self.w.get("drop")
        if not cv:
            return
        c = self.c
        cv.delete("all")
        w, h = cv.winfo_width(), cv.winfo_height()
        col = c["accent"] if hot else c["border"]
        rr(cv, 2, 2, w - 2, h - 2, 16, fill=c["input"], outline=col, width=2, dash=(6, 4))
        cx, cy = w / 2, h / 2 - 22
        cv.create_line(cx, cy + 22, cx, cy - 12, fill=c["accent"], width=4, capstyle="round")
        cv.create_line(cx - 14, cy + 2, cx, cy - 14, cx + 14, cy + 2, fill=c["accent"], width=4,
                       capstyle="round", joinstyle="round")
        cv.create_line(cx - 22, cy + 30, cx + 22, cy + 30, fill=c["accent"], width=4, capstyle="round")
        cv.create_text(cx, cy + 62, text=T("drop_here"), font=F(13, "bold"), fill=c["text"])
        cv.create_text(cx, cy + 84, text=T("or_browse"), font=F(10), fill=c["muted"])

    def targets(self):
        if self.server:
            return [(cc["side"], cc["name"]) for cc in self.clients]
        if self.client and self.client.connected:
            return [("host", T("host_word"))]
        return []

    def render_targets(self):
        r = self.w.get("targets")
        if not r:
            return
        for ch in r.winfo_children():
            ch.destroy()
        tg = self.targets()
        if not tg:
            tk.Label(r, text=T("no_target"), font=F(10), bg=self.c["card"], fg=self.c["warn"]).pack(side="left")
            return
        if self.f_target.get() not in [k for k, _ in tg]:
            self.f_target.set(tg[0][0])
        Segmented(r, self.c, tg, self.f_target).pack()

    def browse(self):
        paths = filedialog.askopenfilenames(parent=self.root)
        if paths:
            self.send_paths(list(paths))

    def on_drop(self, event):
        self.draw_drop(False)
        self.send_paths(list(self.root.tk.splitlist(event.data)))

    def send_paths(self, paths):
        tg = self.targets()
        if not tg:
            return self.notice(T("no_target"), "warn")
        key = self.f_target.get()
        name = dict(tg).get(key, "")
        self.notice(T("sending", n=len(paths), name=name), "accent")
        if self.server:
            conn = self.server.clients.get(key)
            if conn:
                self.server.send_files_to(conn, paths)
        elif self.client:
            def work():
                try:
                    self.client.push_files(paths)
                    self.q.put(("notice", T("sent_ok", name=name), "ok"))
                except Exception as e:
                    self.q.put(("notice", T("log_error", msg=e), "danger"))
            threading.Thread(target=work, daemon=True).start()

    def render_received(self):
        fr = self.w.get("recv")
        if not fr:
            return
        c = self.c
        for ch in fr.winfo_children():
            ch.destroy()
        if not self.received:
            tk.Label(fr, text=T("nothing_received"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
            return
        for tm, who, pth in self.received[:7]:
            r = tk.Frame(fr, bg=c["card"], cursor="hand2")
            r.pack(fill="x", pady=2)
            tk.Label(r, text=tm, font=("Consolas", 9), bg=c["card"], fg=c["muted"]).pack(side="left")
            tk.Label(r, text=os.path.basename(pth), font=F(10), bg=c["card"], fg=c["text"]).pack(side="left", padx=10)
            tk.Label(r, text=who, font=F(9), bg=c["card"], fg=c["muted"]).pack(side="left")
            for wdg in [r] + list(r.winfo_children()):
                wdg.bind("<Button-1>", lambda e, p_=pth: subprocess.Popen(["explorer", "/select,", p_]))

    # ================================================================ REMOTE page
    def page_remote(self, p):
        c = self.c
        self.header(p, T("remote_title"), T("remote_sub"))
        cols = tk.Frame(p, bg=c["bg"])
        cols.pack(fill="both", expand=True, padx=28)
        cols.columnconfigure(0, weight=1, uniform="a")
        cols.columnconfigure(1, weight=1, uniform="a")
        cols.rowconfigure(0, weight=1)

        L = self.card(cols)
        L.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 8))
        li = L.inner
        tk.Label(li, text=T("your_id"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
        tk.Label(li, text=config.fmt_id(self.cfg["device_id"]), font=("Consolas", 26, "bold"),
                 bg=c["card"], fg=c["accent"]).pack(anchor="w", pady=(0, 10))
        tk.Label(li, text=T("password"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
        pr = tk.Frame(li, bg=c["card"])
        pr.pack(anchor="w", pady=(2, 10))
        self.pw_var = tk.StringVar(value=self.cfg["remote_password"])
        e = make_entry(pr, c, self.pw_var, 11, show="" if self.pw_shown else "•", mono=True, state="readonly")
        e.pack(side="left")
        self.w["pw_entry"] = e.entry
        self.w["pw_btn"] = RButton(pr, c, T("hide") if self.pw_shown else T("show"), self.toggle_pw, "ghost", padx=12, height=32)
        self.w["pw_btn"].pack(side="left", padx=6)
        RButton(pr, c, T("copy"), lambda: self.copy(self.cfg["remote_password"]), "ghost", padx=12, height=32).pack(side="left")
        RButton(pr, c, T("regen"), self.new_remote_pw, "ghost", padx=12, height=32).pack(side="left", padx=6)
        self.toggle_row(li, T("allow_remote"), self.allow_var, self.on_allow)
        self.toggle_row(li, T("ask_approval"), self.approval_var, self.save)
        tk.Label(li, text=T("sessions"), font=F(10, "bold"), bg=c["card"], fg=c["text"]).pack(anchor="w", pady=(10, 4))
        self.w["sess"] = tk.Frame(li, bg=c["card"])
        self.w["sess"].pack(fill="x")

        R = self.card(cols, T("connect_title"))
        R.grid(row=0, column=1, sticky="nsew", padx=(8, 0), pady=(0, 8))
        ri = R.inner
        tk.Label(ri, text=T("partner"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
        make_entry(ri, c, self.partner_var, 28, mono=True).pack(anchor="w", pady=(2, 8))
        tk.Label(ri, text=T("partner_pw"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
        make_entry(ri, c, self.ppw_var, 28, mono=True, show="•").pack(anchor="w", pady=(2, 10))
        RButton(ri, c, T("connect"), self.do_connect, width=150).pack(anchor="w")
        tk.Label(ri, text=T("nearby"), font=F(10, "bold"), bg=c["card"], fg=c["text"]).pack(anchor="w", pady=(16, 4))
        self.w["nearby"] = tk.Frame(ri, bg=c["card"])
        self.w["nearby"].pack(fill="x")

    def toggle_pw(self):
        self.pw_shown = not self.pw_shown
        self.w["pw_entry"].config(show="" if self.pw_shown else "•")
        self.w["pw_btn"].set_text(T("hide") if self.pw_shown else T("show"))

    def new_remote_pw(self):
        self.cfg["remote_password"] = secrets.token_urlsafe(6).replace("-", "A").replace("_", "B")[:8].upper()
        self.pw_var.set(self.cfg["remote_password"])
        if self.rhost:
            self.rhost.password = self.cfg["remote_password"]
        config.save(self.cfg)

    def on_allow(self):
        self.cfg["remote_enabled"] = self.allow_var.get()
        config.save(self.cfg)
        self.start_remote() if self.allow_var.get() else self.stop_remote()
        self.render_sessions()

    def render_sessions(self):
        fr = self.w.get("sess")
        if not fr:
            return
        c = self.c
        for ch in fr.winfo_children():
            ch.destroy()
        if not self.sessions:
            tk.Label(fr, text=(T("remote_on") if self.rhost else T("remote_off")) + "  -  " + T("none"),
                     font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
            return
        for s in self.sessions:
            r = tk.Frame(fr, bg=c["card"])
            r.pack(fill="x", pady=3)
            tk.Label(r, text=f"{s['name']}  {s['ip']}", font=F(10), bg=c["card"], fg=c["text"]).pack(side="left")
            RButton(r, c, T("disconnect"), lambda i=s["id"]: self.rhost and self.rhost.disconnect(i),
                    "danger", height=30).pack(side="right")

    def refresh_nearby(self):
        fr = self.w.get("nearby")
        if not fr or self.cur != "remote":
            return
        c = self.c
        peers = [p for p in self.disc.list(self.cfg["device_id"]) if p.get("remote_port")]
        sig = tuple((p["id"], p["ip"]) for p in peers)
        if getattr(self, "_nsig", None) == sig and fr.winfo_children():
            return
        self._nsig = sig
        for ch in fr.winfo_children():
            ch.destroy()
        if not peers:
            tk.Label(fr, text=T("no_nearby"), font=F(10), bg=c["card"], fg=c["muted"]).pack(anchor="w")
        for pr in peers:
            r = tk.Frame(fr, bg=c["card2"], cursor="hand2")
            r.pack(fill="x", pady=3)
            a = tk.Label(r, text=pr["name"], font=F(10, "bold"), bg=c["card2"], fg=c["text"], padx=10, pady=6)
            a.pack(side="left")
            b = tk.Label(r, text=f"{config.fmt_id(pr['id'])}   {pr['ip']}", font=("Consolas", 9),
                         bg=c["card2"], fg=c["muted"])
            b.pack(side="left")
            for wdg in (r, a, b):
                wdg.bind("<Button-1>", lambda e, p_=pr: self.partner_var.set(str(p_["id"])))

    def do_connect(self):
        target, pw = self.partner_var.get().strip(), self.ppw_var.get().strip()
        if not target or not pw:
            return
        digits = target.replace(" ", "")
        host, port = target, int(self.cfg["remote_port"])
        found = False
        for pr in self.disc.list(self.cfg["device_id"]):
            if digits == str(pr["id"]) or target == pr["ip"]:
                host, port, found = pr["ip"], pr.get("remote_port") or port, True
        if digits.isdigit() and len(digits) == 9 and not found:
            return self.notice(T("r_failed", msg="ID not found on this network"), "danger")
        self.notice(T("st_connecting"), "warn")

        def work():
            v = RemoteViewer(host, port, pw, on_close=lambda: None)
            try:
                v.connect(self.cfg["device_name"].replace(" ", "_"))
                self.q.put(("viewer", v, host))
            except Exception as e:
                self.q.put(("notice", T("r_failed", msg=e), "danger"))
        threading.Thread(target=work, daemon=True).start()

    # ================================================================ SETTINGS page
    def page_settings(self, p):
        c = self.c
        self.header(p, T("settings_title"), "")
        card = self.card(p)
        card.pack(fill="x", padx=28, pady=(0, 14))
        r = self.row(card.inner, T("language"))
        Segmented(r, c, [("en", "English"), ("tr", "Türkçe")], self.lang_var, self.on_lang).pack()
        r = self.row(card.inner, T("theme"))
        Segmented(r, c, [("dark", T("theme_dark")), ("light", T("theme_light")), ("auto", T("theme_auto"))],
                  self.theme_var, self.on_theme).pack()
        self.toggle_row(card.inner, T("autostart"), self.autostart_var, self.on_autostart)
        self.toggle_row(card.inner, T("auto_update"), self.autoupd_var, self.save)

        c2 = self.card(p)
        c2.pack(fill="x", padx=28, pady=(0, 14))
        bt = tk.Frame(c2.inner, bg=c["card"])
        bt.pack(fill="x")
        RButton(bt, c, T("install_here"), self.do_install, "ghost").pack(side="left", padx=(0, 8))
        RButton(bt, c, T("firewall"), self.do_firewall, "ghost").pack(side="left", padx=(0, 8))
        if not is_admin():
            RButton(bt, c, T("admin"), self.do_admin, "ghost").pack(side="left")
        tk.Label(c2.inner, text=T("firewall_hint") + "  " + T("admin_hint"), font=F(9), bg=c["card"],
                 fg=c["muted"], wraplength=720, justify="left").pack(anchor="w", pady=(8, 0))

        c3 = self.card(p, T("about"))
        c3.pack(fill="x", padx=28, pady=(0, 14))
        tk.Label(c3.inner, text=f"{APP_NAME}   {T('version')} {__version__}", font=F(10), bg=c["card"],
                 fg=c["text"]).pack(anchor="w")
        ur = tk.Frame(c3.inner, bg=c["card"])
        ur.pack(fill="x", pady=(10, 0))
        RButton(ur, c, T("check_updates"), lambda: self.check_updates(True), "primary").pack(side="left")
        self.w["upd"] = tk.Label(ur, text=self.upd_text, font=F(10), bg=c["card"], fg=c["muted"])
        self.w["upd"].pack(side="left", padx=14)

    def on_lang(self):
        self.cfg["lang"] = self.lang_var.get()
        i18n.set_lang(self.cfg["lang"])
        self.save()
        self.build()

    def on_theme(self):
        self.cfg["theme"] = self.theme_var.get()
        self.save()
        self.build()

    def on_autostart(self):
        try:
            config.set_autostart(self.autostart_var.get())
        except Exception as e:
            self.autostart_var.set(False)
            self.notice(str(e), "danger")

    def do_install(self):
        try:
            dst = config.install_here()
            self.autostart_var.set(True)
            self.notice(T("installed", path=dst), "ok")
        except Exception as e:
            self.notice(str(e), "danger")

    def do_firewall(self):
        run_elevated("cmd.exe", config.firewall_command(int(self.cfg["port"]), int(self.cfg["remote_port"]),
                                                          DISCOVERY_PORT))

    def do_admin(self):
        exe = sys.executable
        args = "" if getattr(sys, "frozen", False) else f'"{os.path.abspath(sys.argv[0])}"'
        if run_elevated(exe, args):
            self.quit()

    # ================================================================ updates
    def check_updates(self, manual=False):
        self.q.put(("upd", "..."))

        def work():
            try:
                if GITHUB_REPO.startswith("OWNER/"):
                    self.q.put(("upd", T("update_norepo")))
                    return
                info = updater.check()
                if not info:
                    self.q.put(("upd", T("up_to_date")))
                    return
                self.update_info = info
                self.q.put(("upd", T("update_found", v=info["version"])))
                if manual or self.cfg["auto_update"]:
                    self.install_update(info)
            except Exception as e:
                self.q.put(("upd", T("update_err", msg=e)))
        threading.Thread(target=work, daemon=True).start()

    def busy(self):
        return bool(self.server and self.server.active) or bool(self.sessions) or bool(self.viewers)

    def install_update(self, info):
        while self.busy():                      # never interrupt an active session
            time.sleep(30)
        self.q.put(("upd", T("updating", v=info["version"])))
        path = updater.download(info)
        updater.apply_and_restart(path)
        self.q.put(("quit",))

    def auto_update_loop(self):
        if self.cfg["auto_update"] and getattr(sys, "frozen", False):
            self.check_updates(False)
        self.root.after(6 * 3600 * 1000, self.auto_update_loop)

    # ================================================================ lifecycle
    def quit(self):
        try:
            self.save()
        except Exception:
            pass
        for o in (self.server, self.client, self.rhost, self.disc):
            try:
                o and o.stop()
            except Exception:
                pass
        self.root.destroy()
        os._exit(0)

    def _shot(self, path):
        from PIL import ImageGrab
        page = os.environ.get("FRAMIO_PAGE")
        if page:
            self.show(page)
            self.cur = page
            self.root.update()
        self.root.update()
        x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
        ImageGrab.grab(bbox=(x, y, x + self.root.winfo_width(), y + self.root.winfo_height())).save(path)
        self.quit()

    def run(self):
        self.root.mainloop()


class ViewerWindow:
    """Remote desktop viewer window."""

    def __init__(self, app, viewer, host):
        self.app, self.v = app, viewer
        c = app.c
        self.top = tk.Toplevel(app.root)
        self.top.title(T("viewer_title", name=host))
        self.top.geometry("1100x700")
        self.top.configure(bg="#000000")
        bar = tk.Frame(self.top, bg=c["card"])
        bar.pack(fill="x")
        tk.Label(bar, text=host, font=F(10, "bold"), bg=c["card"], fg=c["text"]).pack(side="left", padx=12, pady=6)
        RButton(bar, c, T("disconnect"), self.close, "danger", height=28).pack(side="right", padx=8, pady=4)
        RButton(bar, c, T("fullscreen"), self.fullscreen, "ghost", height=28).pack(side="right", pady=4)
        self.cv = tk.Canvas(self.top, bg="#000000", highlightthickness=0, cursor="crosshair")
        self.cv.pack(fill="both", expand=True)
        self.img_id, self.photo, self.seq = None, None, -1
        self.geom = (0, 0, 1, 1)
        self.last_move, self.pending = 0.0, None
        t = self.top
        self.cv.bind("<Motion>", self.on_move)
        for b, code in ((1, 0), (3, 1), (2, 2)):
            self.cv.bind(f"<ButtonPress-{b}>", lambda e, k=code: self.on_btn(e, k, 1))
            self.cv.bind(f"<ButtonRelease-{b}>", lambda e, k=code: self.on_btn(e, k, 0))
        self.cv.bind("<MouseWheel>", lambda e: self.v.send_input(f"W {e.delta}"))
        t.bind("<KeyPress>", lambda e: self.on_key(e, 1))
        t.bind("<KeyRelease>", lambda e: self.on_key(e, 0))
        t.bind("<FocusOut>", lambda e: self.v.send_input("R"))
        t.bind("<F11>", lambda e: self.fullscreen())
        t.protocol("WM_DELETE_WINDOW", self.close)
        self.cv.focus_set()
        try:
            set_dark_titlebar(ctypes.windll.user32.GetParent(t.winfo_id()), True)
        except Exception:
            pass
        app.viewers.append(self)
        self.poll()

    def fullscreen(self):
        self.top.attributes("-fullscreen", not self.top.attributes("-fullscreen"))

    def close(self):
        self.v.close()
        if self in self.app.viewers:
            self.app.viewers.remove(self)
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    def poll(self):
        if self.v.closed:
            return self.close()
        fr = self.v.latest
        cw, ch = max(1, self.cv.winfo_width()), max(1, self.cv.winfo_height())
        if fr and (self.v.seq != self.seq or (cw, ch) != getattr(self, "_size", None)):
            self.seq, self._size = self.v.seq, (cw, ch)
            w, h, jpg = fr
            im = Image.open(io.BytesIO(jpg))
            s = min(cw / w, ch / h)
            dw, dh = max(1, int(w * s)), max(1, int(h * s))
            im = im.resize((dw, dh), Image.BILINEAR)
            self.photo = ImageTk.PhotoImage(im)
            ox, oy = (cw - dw) // 2, (ch - dh) // 2
            self.geom = (ox, oy, dw, dh)
            if self.img_id is None:
                self.img_id = self.cv.create_image(ox, oy, anchor="nw", image=self.photo)
            else:
                self.cv.coords(self.img_id, ox, oy)
                self.cv.itemconfig(self.img_id, image=self.photo)
        self.top.after(25, self.poll)

    def norm(self, e):
        ox, oy, dw, dh = self.geom
        nx = min(65535, max(0, int((e.x - ox) / max(1, dw) * 65535)))
        ny = min(65535, max(0, int((e.y - oy) / max(1, dh) * 65535)))
        return nx, ny

    def on_move(self, e):
        now = time.time()
        if now - self.last_move < 0.012:
            if self.pending is None:
                self.pending = self.top.after(15, self.flush_move)
            self._lastev = e
            return
        self.last_move = now
        self.v.send_input("M %d %d" % self.norm(e))

    def flush_move(self):
        self.pending = None
        e = getattr(self, "_lastev", None)
        if e is not None:
            self.last_move = time.time()
            self.v.send_input("M %d %d" % self.norm(e))

    def on_btn(self, e, code, down):
        self.v.send_input("M %d %d" % self.norm(e))
        self.v.send_input(f"B {code} {down}")

    def on_key(self, e, down):
        vk = e.keycode
        if 1 <= vk <= 254:
            self.v.send_input(f"K {vk} 0 {1 if vk in EXTENDED_VKS else 0} {down}")
        return "break"
