"""Small themed Tk widgets: rounded buttons, toggles, segmented control, entries, dialogs."""
import tkinter as tk
from tkinter import font as tkfont

FONT = "Segoe UI"


def rr(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def F(size=10, weight="normal"):
    return (FONT, size, weight)


class RButton(tk.Canvas):
    def __init__(self, parent, c, text, command=None, kind="primary", padx=18, height=36, width=None):
        self.c, self.kind, self.command, self.text = c, kind, command, text
        self.font = tkfont.Font(family=FONT, size=10, weight="bold")
        self.padx, self.h = padx, height
        w = width or self.font.measure(text) + 2 * padx
        super().__init__(parent, width=w, height=height, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.hover, self.enabled = False, True
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<Button-1>", self._click)
        self.draw()

    def _colors(self):
        c = self.c
        if not self.enabled:
            return c["card2"], c["muted"]
        if self.kind == "primary":
            return (c["accent_hover"] if self.hover else c["accent"]), c["accent_fg"]
        if self.kind == "danger":
            return c["danger"], "#FFFFFF"
        return (c["border"] if self.hover else c["card2"]), c["text"]

    def draw(self):
        self.delete("all")
        w, h = int(self["width"]), self.h
        bg, fg = self._colors()
        rr(self, 1, 1, w - 1, h - 1, 10, fill=bg, outline=bg)
        self.create_text(w / 2, h / 2, text=self.text, fill=fg, font=self.font)

    def _set_hover(self, v):
        self.hover = v
        self.draw()

    def _click(self, _e):
        if self.enabled and self.command:
            self.command()

    def set_text(self, text):
        self.text = text
        self.config(width=self.font.measure(text) + 2 * self.padx)
        self.draw()

    def set_enabled(self, v):
        self.enabled = v
        self.config(cursor="hand2" if v else "arrow")
        self.draw()


class Toggle(tk.Canvas):
    def __init__(self, parent, c, var, command=None):
        super().__init__(parent, width=44, height=24, bg=parent.cget("bg"),
                         highlightthickness=0, bd=0, cursor="hand2")
        self.c, self.var, self.command = c, var, command
        self.bind("<Button-1>", self._click)
        self.draw()

    def draw(self):
        self.delete("all")
        on = bool(self.var.get())
        rr(self, 1, 2, 43, 22, 10, fill=self.c["accent"] if on else self.c["border"],
           outline=self.c["accent"] if on else self.c["border"])
        x = 32 if on else 12
        self.create_oval(x - 8, 4, x + 8, 20, fill="#FFFFFF" if on else self.c["muted"], outline="")

    def _click(self, _e):
        self.var.set(not self.var.get())
        self.draw()
        if self.command:
            self.command()


class Segmented(tk.Frame):
    def __init__(self, parent, c, options, var, command=None):
        super().__init__(parent, bg=c["card2"], padx=3, pady=3)
        self.c, self.var, self.command, self.labels = c, var, command, {}
        for val, text in options:
            lb = tk.Label(self, text=text, font=F(10, "bold"), padx=14, pady=5, cursor="hand2")
            lb.pack(side="left")
            lb.bind("<Button-1>", lambda e, v=val: self.select(v))
            self.labels[val] = lb
        self.paint()

    def paint(self):
        for val, lb in self.labels.items():
            on = self.var.get() == val
            lb.config(bg=self.c["accent"] if on else self.c["card2"],
                      fg=self.c["accent_fg"] if on else self.c["muted"])

    def select(self, v):
        self.var.set(v)
        self.paint()
        if self.command:
            self.command()


def make_entry(parent, c, var, width=26, show=None, mono=False, state="normal"):
    fr = tk.Frame(parent, bg=c["border"], padx=1, pady=1)
    e = tk.Entry(fr, textvariable=var, width=width, relief="flat", bd=0, bg=c["input"], fg=c["text"],
                 insertbackground=c["text"], disabledbackground=c["input"], disabledforeground=c["muted"], readonlybackground=c["input"],
                 font=("Consolas", 11) if mono else F(10), show=show or "", state=state,
                 selectbackground=c["accent"], selectforeground=c["accent_fg"])
    e.pack(ipady=6, ipadx=6)
    e.bind("<FocusIn>", lambda _e: fr.config(bg=c["accent"]))
    e.bind("<FocusOut>", lambda _e: fr.config(bg=c["border"]))
    fr.entry = e
    return fr


def modal(root, c, title, message, yes, no=None, danger=False):
    """Themed modal dialog. Returns True if the primary button was pressed."""
    top = tk.Toplevel(root)
    top.title(title)
    top.configure(bg=c["card"])
    top.transient(root)
    top.resizable(False, False)
    res = {"v": False}
    tk.Label(top, text=title, font=F(13, "bold"), bg=c["card"], fg=c["text"]).pack(
        anchor="w", padx=24, pady=(22, 4))
    tk.Label(top, text=message, font=F(10), bg=c["card"], fg=c["muted"], justify="left",
             wraplength=380).pack(anchor="w", padx=24, pady=(0, 18))
    row = tk.Frame(top, bg=c["card"])
    row.pack(anchor="e", padx=24, pady=(0, 20))

    def done(v):
        res["v"] = v
        top.destroy()
    if no:
        RButton(row, c, no, lambda: done(False), kind="ghost").pack(side="left", padx=(0, 8))
    RButton(row, c, yes, lambda: done(True), kind="danger" if danger else "primary").pack(side="left")
    top.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - top.winfo_width()) // 2
    y = root.winfo_rooty() + (root.winfo_height() - top.winfo_height()) // 3
    top.geometry(f"+{max(0, x)}+{max(0, y)}")
    top.protocol("WM_DELETE_WINDOW", lambda: done(False))
    try:
        from .winapi import set_dark_titlebar
        import ctypes
        set_dark_titlebar(ctypes.windll.user32.GetParent(top.winfo_id()), c["bg"] == "#0A1020")
    except Exception:
        pass
    top.grab_set()
    top.focus_force()
    root.wait_window(top)
    return res["v"]
