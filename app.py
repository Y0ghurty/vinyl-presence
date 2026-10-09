"""Vinyl Presence: show the record on your turntable as Discord Rich Presence.

A normal desktop window, nothing is hosted. Double-click start.bat (or run
`pythonw app.py`), type a few letters of the record, press Enter.
"""
import ctypes
import hashlib
import io
import json
import queue
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from core import ASSETS, DATA, ROOT, Core, search
from metadata import USER_AGENT
from version import GITHUB_REPO, VERSION

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:  # covers in the window need Pillow; everything else works without it
    Image = None

FONT = "Segoe UI"
BG, PANEL, PANEL2, ROW_ALT, LINE = "#121014", "#1b181e", "#27232b", "#1f1c22", "#363039"
TEXT, MUTED, FAINT = "#f2ebe5", "#a0978f", "#6d6660"
ACCENT, ACCENT_HI, ACCENT_DIM = "#ef7a3c", "#ff9259", "#5c3524"
GOOD, WARN, BAD = "#46c98b", "#f2b14c", "#ec5f5f"

SUPPORT_DISCORD = "https://discord.gg/xHj9Td7MYz"  # Vinyl Hangout: questions & help
SORTS = ["Artist A–Z", "Title A–Z", "Recently added", "Recently played", "Most played"]
CARD_TITLES = {
    "app": "Listening to <app name>",
    "artist": "Listening to <artist>",
    "album": "Listening to <album>",
    "custom": "Listening to <my own text>",
}
STATUS_CHOICES = {
    "artist": "Listening to <artist>",
    "title": "Listening to <track or album>",
    "app": "Listening to <card title>",
}
DISCORD_STATES = {
    "connected": (GOOD, "Discord connected"),
    "connecting": (WARN, "Connecting to Discord…"),
    "starting": (WARN, "Connecting to Discord…"),
    "not_running": (BAD, "Discord isn't running"),
    "no_client_id": (WARN, "Setup needed: click here"),
    "bad_client_id": (BAD, "Discord rejected the Application ID"),
}


def fmt_time(sec):
    sec = max(0, int(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}" if sec >= 3600 else f"{sec // 60}:{sec % 60:02d}"


def side_name(side):
    return f"Side {side}" if side and len(side) <= 2 else (side or "")


def short_format(fmt):
    return fmt[7:] if fmt.startswith("Vinyl, ") else fmt


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def vinyl_image(size):
    path = ASSETS / "vinyl.png"
    if Image:
        return ImageTk.PhotoImage(Image.open(path).resize((size, size), Image.LANCZOS))
    return tk.PhotoImage(file=str(path)).subsample(max(1, round(1024 / size)))


class VinylApp:
    def __init__(self, root, core):
        self.root, self.core = root, core
        self.ui = queue.Queue()  # callables queued by worker threads, run on the Tk thread
        self.scale = root.winfo_fpixels("1i") / 96
        self.loading = None
        self.now_sig = object()
        self.playing_iid = None
        self.covers = {}
        self.cover_url = None
        self.search_job = None
        self.settings = None
        self.ticks = 0

        self._style()
        self._build()
        self.refresh_list()
        self.entry.focus_set()
        self.root.after(150, self._loop)
        threading.Thread(target=self._check_update, daemon=True, name="update-check").start()
        if not core.config["discord_client_id"]:
            self.root.after(600, self.open_settings)

    def px(self, v):
        return int(v * self.scale)

    # ------------------------------------------------------------ look & feel

    def _style(self):
        st = ttk.Style(self.root)
        st.theme_use("clam")
        row_h = int(tkfont.Font(family=FONT, size=10).metrics("linespace") * 1.9)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT, borderwidth=0,
                     rowheight=row_h, font=(FONT, 10))
        st.map("Treeview", background=[("selected", ACCENT_DIM)], foreground=[("selected", TEXT)])
        st.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Treeview.Heading", background=PANEL, foreground=FAINT, relief="flat", borderwidth=0,
                     font=(FONT, 9, "bold"), padding=(self.px(6), self.px(4)))
        st.map("Treeview.Heading", background=[("active", PANEL)], foreground=[("active", MUTED)])
        st.configure("Vertical.TScrollbar", troughcolor=PANEL, background=PANEL2, bordercolor=PANEL,
                     lightcolor=PANEL2, darkcolor=PANEL2, arrowcolor=MUTED, gripcount=0)
        st.map("Vertical.TScrollbar", background=[("active", LINE)])
        st.configure("TCombobox", fieldbackground=PANEL2, background=PANEL2, foreground=TEXT, arrowcolor=MUTED,
                     bordercolor=LINE, lightcolor=PANEL2, darkcolor=PANEL2, selectbackground=PANEL2,
                     selectforeground=TEXT, padding=self.px(4))
        st.map("TCombobox", fieldbackground=[("readonly", PANEL2)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", PANEL2)], selectforeground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", PANEL2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DIM)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", (FONT, 10))

    def btn(self, parent, text, command, kind="normal", size=10):
        bg, fg, hover = {"normal": (PANEL2, TEXT, LINE), "accent": (ACCENT, "#1d120b", ACCENT_HI),
                         "ghost": (PANEL, MUTED, PANEL2)}[kind]
        b = tk.Label(parent, text=text, bg=bg, fg=fg, cursor="hand2", padx=self.px(12), pady=self.px(5),
                     font=(FONT, size, "bold" if kind == "accent" else "normal"))
        b.base_bg, b.hover_bg = bg, hover
        b.bind("<Button-1>", lambda e: command())
        b.bind("<Enter>", lambda e: b.config(bg=b.hover_bg))
        b.bind("<Leave>", lambda e: b.config(bg=b.base_bg))
        return b

    def make_entry(self, parent, var, show=None, width=34, size=10):
        return tk.Entry(parent, textvariable=var, show=show, width=width, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                        relief="flat", font=(FONT, size), highlightthickness=1, highlightbackground=LINE,
                        highlightcolor=ACCENT)

    def link(self, parent, text, url, bg=PANEL):
        lbl = tk.Label(parent, text=text, bg=bg, fg=ACCENT, cursor="hand2", font=(FONT, 9, "underline"))
        lbl.bind("<Button-1>", lambda e: webbrowser.open(url))
        return lbl

    # ------------------------------------------------------------ layout

    def _build(self):
        root, px = self.root, self.px

        # header
        top = tk.Frame(root, bg=BG)
        top.pack(fill="x", padx=px(20), pady=(px(14), px(10)))
        self.icon_small = vinyl_image(px(26))
        tk.Label(top, image=self.icon_small, bg=BG).pack(side="left")
        tk.Label(top, text="Vinyl Presence", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(side="left", padx=px(10))
        self.btn(top, "⚙  Settings", self.open_settings).pack(side="right")
        self.btn(top, "Help", lambda: webbrowser.open(SUPPORT_DISCORD), kind="ghost").pack(side="right", padx=(0, px(6)))
        pill = tk.Frame(top, bg=BG, cursor="hand2")
        pill.pack(side="right", padx=px(16))
        self.dc_dot = tk.Label(pill, text="●", bg=BG, fg=WARN, font=(FONT, 10), cursor="hand2")
        self.dc_dot.pack(side="left")
        self.dc_text = tk.Label(pill, text="Starting…", bg=BG, fg=MUTED, font=(FONT, 10), cursor="hand2")
        self.dc_text.pack(side="left", padx=(px(4), 0))
        self.update_lbl = tk.Label(top, bg=BG, fg=ACCENT, cursor="hand2", font=(FONT, 10, "underline"))
        for w in (pill, self.dc_dot, self.dc_text):
            w.bind("<Button-1>", lambda e: self.open_settings())

        # now playing bar
        nowf = tk.Frame(root, bg=PANEL)
        nowf.pack(fill="x", padx=px(20))
        inner = tk.Frame(nowf, bg=PANEL)
        inner.pack(fill="x", padx=px(18), pady=px(16))
        self.cover_size = px(132)
        self.vinyl_cover = vinyl_image(self.cover_size)
        self.cover_lbl = tk.Label(inner, image=self.vinyl_cover, bg=PANEL, bd=0)
        self.cover_lbl.pack(side="left", anchor="n")
        info = tk.Frame(inner, bg=PANEL)
        info.pack(side="left", fill="both", expand=True, padx=(px(20), 0))
        info.columnconfigure(0, weight=1)
        self.eyebrow = tk.Label(info, bg=PANEL, fg=ACCENT, font=(FONT, 9, "bold"), anchor="w")
        self.eyebrow.grid(row=0, column=0, sticky="ew")
        self.title_lbl = tk.Label(info, bg=PANEL, fg=TEXT, font=(FONT, 18, "bold"), anchor="w", justify="left")
        self.title_lbl.grid(row=1, column=0, sticky="ew")
        self.sub_lbl = tk.Label(info, bg=PANEL, fg=MUTED, font=(FONT, 11), anchor="w", justify="left")
        self.sub_lbl.grid(row=2, column=0, sticky="ew")
        info.bind("<Configure>", lambda e: (self.title_lbl.config(wraplength=e.width),
                                            self.sub_lbl.config(wraplength=e.width)))

        self.prog = tk.Frame(info, bg=PANEL)
        self.prog.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        self.t_cur = tk.Label(self.prog, bg=PANEL, fg=MUTED, font=(FONT, 9), width=7, anchor="w")
        self.t_cur.pack(side="left")
        self.bar = tk.Canvas(self.prog, height=px(6), bg=PANEL, highlightthickness=0)
        self.bar.pack(side="left", fill="x", expand=True)
        self.t_end = tk.Label(self.prog, bg=PANEL, fg=MUTED, font=(FONT, 9), width=7, anchor="e")
        self.t_end.pack(side="left")
        self.banner = tk.Frame(info, bg=PANEL)
        self.banner_lbl = tk.Label(self.banner, bg=PANEL, fg=TEXT, font=(FONT, 10))
        self.banner_lbl.pack(side="left")
        self.banner_btn = self.btn(self.banner, "", lambda: None, kind="accent")
        self.banner_btn.pack(side="left", padx=px(12))

        ctl = tk.Frame(info, bg=PANEL)
        ctl.grid(row=4, column=0, sticky="ew", pady=(px(12), 0))
        ctl.columnconfigure(1, weight=1)
        self.sides_frame = tk.Frame(ctl, bg=PANEL)
        self.sides_frame.grid(row=0, column=0, sticky="w")
        self.prev_btn = self.btn(ctl, "⏮", lambda: self.bg(self.core.player.control, "prev"), size=11)
        self.prev_btn.grid(row=0, column=2, padx=(0, px(6)))
        self.next_btn = self.btn(ctl, "⏭", lambda: self.bg(self.core.player.control, "next"), size=11)
        self.next_btn.grid(row=0, column=3, padx=(0, px(6)))
        self.stop_btn = self.btn(ctl, "■  Stop", lambda: self.bg(self.core.player.control, "stop"))
        self.stop_btn.grid(row=0, column=4)

        # body: records on the left, tracklist on the right
        body = tk.Frame(root, bg=BG)
        body.pack(fill="both", expand=True, padx=px(20), pady=(px(12), px(16)))
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, minsize=px(340))
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, bg=PANEL)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, px(12)))
        lp = tk.Frame(left, bg=PANEL)
        lp.pack(fill="both", expand=True, padx=px(16), pady=px(14))

        sr = tk.Frame(lp, bg=PANEL)
        sr.pack(fill="x")
        self.search_box = tk.Frame(sr, bg=PANEL2, highlightthickness=1, highlightbackground=LINE)
        self.search_box.pack(side="left", fill="x", expand=True)
        tk.Label(self.search_box, text="⌕", bg=PANEL2, fg=MUTED, font=(FONT, 15)).pack(side="left", padx=(px(10), 0))
        self.q = tk.StringVar()
        self.entry = tk.Entry(self.search_box, textvariable=self.q, bg=PANEL2, fg=TEXT, insertbackground=TEXT,
                              relief="flat", bd=0, font=(FONT, 13))
        self.entry.pack(side="left", fill="x", expand=True, padx=px(8), pady=px(8))
        self.placeholder = tk.Label(self.search_box, text="Search artist, album, label, cat# or year…",
                                    bg=PANEL2, fg=FAINT, font=(FONT, 13))
        self.placeholder.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        self.placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.btn(sr, "Surprise me", self.surprise).pack(side="right", padx=(px(10), 0), fill="y")

        mr = tk.Frame(lp, bg=PANEL)
        mr.pack(fill="x", pady=(px(10), px(4)))
        self.count_lbl = tk.Label(mr, bg=PANEL, fg=MUTED, font=(FONT, 9))
        self.count_lbl.pack(side="left")
        self.sort = ttk.Combobox(mr, values=SORTS, state="readonly", width=15, font=(FONT, 9))
        self.sort.set(self.core.config.get("list_sort") if self.core.config.get("list_sort") in SORTS else SORTS[0])
        self.sort.pack(side="right")
        tk.Label(mr, text="Sort", bg=PANEL, fg=FAINT, font=(FONT, 9)).pack(side="right", padx=px(6))

        tf = tk.Frame(lp, bg=PANEL)
        tf.pack(fill="both", expand=True)
        cols = (("artist", "ARTIST", 200), ("title", "TITLE", 260), ("year", "YEAR", 56), ("format", "FORMAT", 150))
        self.tree = ttk.Treeview(tf, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        for key, label, width in cols:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=px(width), minwidth=px(40), stretch=key in ("artist", "title", "format"))
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("playing", foreground=ACCENT_HI)
        sb = ttk.Scrollbar(tf, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.empty = tk.Frame(tf, bg=PANEL)
        tk.Label(self.empty, text="No records yet", bg=PANEL, fg=TEXT, font=(FONT, 14, "bold")).pack(pady=(0, px(6)))
        tk.Label(self.empty, text="On Discogs: Collection → Export → download the CSV.\n"
                                  "Then import it here (or drop the .csv in the app folder).",
                 bg=PANEL, fg=MUTED, font=(FONT, 10), justify="center").pack()
        self.btn(self.empty, "Import Discogs CSV…", self.import_csv, kind="accent").pack(pady=px(14))

        ft = tk.Frame(lp, bg=PANEL)
        ft.pack(fill="x", pady=(px(8), 0))
        tk.Label(ft, text="Enter or double-click: play     ↑ ↓: choose     Esc: clear", bg=PANEL, fg=FAINT,
                 font=(FONT, 9)).pack(side="left")
        self.meta_lbl = tk.Label(ft, bg=PANEL, fg=FAINT, font=(FONT, 9))
        self.meta_lbl.pack(side="right")

        right = tk.Frame(body, bg=PANEL)
        right.grid(row=0, column=1, sticky="nsew")
        rp = tk.Frame(right, bg=PANEL)
        rp.pack(fill="both", expand=True, padx=px(16), pady=px(14))
        tk.Label(rp, text="TRACKLIST", bg=PANEL, fg=FAINT, font=(FONT, 9, "bold"), anchor="w").pack(fill="x")
        tl = tk.Frame(rp, bg=PANEL)
        tl.pack(fill="both", expand=True, pady=(px(6), 0))
        self.tracks = ttk.Treeview(tl, columns=("pos", "title", "dur"), show="", selectmode="browse")
        self.tracks.column("pos", width=px(40), stretch=False)
        self.tracks.column("title", width=px(200))
        self.tracks.column("dur", width=px(52), stretch=False, anchor="e")
        self.tracks.tag_configure("side", foreground=FAINT, font=(FONT, 9, "bold"))
        self.tracks.tag_configure("done", foreground=FAINT)
        self.tracks.tag_configure("current", foreground=ACCENT_HI, font=(FONT, 10, "bold"))
        tsb = ttk.Scrollbar(tl, orient="vertical", command=self.tracks.yview)
        self.tracks.configure(yscrollcommand=tsb.set)
        self.tracks.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tracks_msg = tk.Label(tl, bg=PANEL, fg=FAINT, font=(FONT, 10), justify="center")
        tk.Label(rp, text="Double-click a track or side to jump there", bg=PANEL, fg=FAINT,
                 font=(FONT, 9), anchor="w").pack(fill="x", pady=(px(8), 0))

        # behaviour
        self.q.trace_add("write", lambda *a: self._schedule_search())
        self.entry.bind("<FocusIn>", lambda e: self.search_box.config(highlightbackground=ACCENT))
        self.entry.bind("<FocusOut>", lambda e: self.search_box.config(highlightbackground=LINE))
        self.entry.bind("<Down>", lambda e: self.move_selection(1))
        self.entry.bind("<Up>", lambda e: self.move_selection(-1))
        self.entry.bind("<Next>", lambda e: self.move_selection(10))
        self.entry.bind("<Prior>", lambda e: self.move_selection(-10))
        self.entry.bind("<Return>", lambda e: self.play_selected())
        self.tree.bind("<Double-1>", self._on_tree_double)
        self.tree.bind("<Return>", lambda e: self.play_selected())
        self.tree.bind("<Key>", self._type_into_search)
        self.tracks.bind("<Double-1>", lambda e: self.play_track_row(self.tracks.identify_row(e.y)))
        self.tracks.bind("<Return>", lambda e: self.play_track_row(self.tracks.focus()))
        self.sort.bind("<<ComboboxSelected>>", self._on_sort)
        root.bind("<Control-f>", lambda e: (self.entry.focus_set(), self.entry.select_range(0, "end")))
        root.bind("<Escape>", lambda e: (self.q.set(""), self.entry.focus_set()))

    # ------------------------------------------------------------ record list

    def _schedule_search(self):
        self.placeholder.place_forget() if self.q.get() else self.placeholder.place(
            in_=self.entry, x=0, rely=0.5, anchor="w")
        if self.search_job:
            self.root.after_cancel(self.search_job)
        self.search_job = self.root.after(90, self.refresh_list)

    def _on_sort(self, _e=None):
        self.core.update_config({"list_sort": self.sort.get()})
        self.refresh_list()
        self.entry.focus_set()

    def refresh_list(self):
        self.search_job = None
        recs = list(self.core.records.values())
        query = self.q.get().strip()
        if query:
            items = search(recs, query)
            self.count_lbl.config(text=f"{len(items)} of {len(recs)} records" if items
                                  else f"Nothing matches “{query}”")
        else:
            plays, last = self.core.play_stats()
            mode = self.sort.get()
            if mode == "Title A–Z":
                items = sorted(recs, key=lambda r: (r["_t"], r["_a"]))
            elif mode == "Recently added":
                items = sorted(recs, key=lambda r: r["added"], reverse=True)
            elif mode == "Recently played":
                items = sorted(recs, key=lambda r: (-(last.get(r["id"]) or 0), r["_a"], r["_t"]))
            elif mode == "Most played":
                items = sorted(recs, key=lambda r: (-plays.get(r["id"], 0), r["_a"], r["_t"]))
            else:
                items = sorted(recs, key=lambda r: (r["_a"], r["year"], r["_t"]))
            self.count_lbl.config(text=f"{len(recs)} records" if recs else "")
        self.tree.delete(*self.tree.get_children())
        playing = self.playing_iid
        for i, r in enumerate(items):
            iid = str(r["id"])
            tags = (("odd",) if i % 2 else ()) + (("playing",) if iid == playing else ())
            self.tree.insert("", "end", iid=iid, tags=tags,
                             values=(r["artist"], r["title"], r["year"], short_format(r["format"])))
        if query and items:
            self.tree.selection_set(str(items[0]["id"]))
            self.tree.see(str(items[0]["id"]))
        if recs:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.45, anchor="center")

    def move_selection(self, delta):
        items = self.tree.get_children()
        if items:
            sel = self.tree.selection()
            i = items.index(sel[0]) + delta if sel else (0 if delta > 0 else len(items) - 1)
            i = max(0, min(len(items) - 1, i))
            self.tree.selection_set(items[i])
            self.tree.see(items[i])
        return "break"

    def _on_tree_double(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell":
            self.play_selected()

    def _type_into_search(self, e):
        if e.char and e.char.isprintable() and not (e.state & 0x4):
            self.entry.focus_set()
            self.entry.insert("end", e.char)
            return "break"
        if e.keysym == "BackSpace":
            self.entry.focus_set()
            if self.q.get():
                self.entry.delete(len(self.q.get()) - 1, "end")
            return "break"

    def surprise(self):
        items = self.tree.get_children()
        if items:
            pick = random.choice(items)
            self.tree.selection_set(pick)
            self.tree.see(pick)
            self.entry.focus_set()
            self.count_lbl.config(text="How about this one? Press Enter to play it.")

    def play_selected(self):
        sel = self.tree.selection()
        if sel:
            self.play(int(sel[0]))
        return "break"

    # ------------------------------------------------------------ playing

    def bg(self, fn, *args):
        """Run a player action off the UI thread (it may look things up online)."""
        def work():
            try:
                fn(*args)
            except Exception as e:
                msg = str(e).strip("'\"")
                self.ui.put(lambda: messagebox.showerror("Vinyl Presence", msg, parent=self.root))
        threading.Thread(target=work, daemon=True).start()

    def play(self, rid, side=None, track=None):
        snap = self.core.player.snapshot()
        if not snap or snap["record"]["id"] != rid:
            self.loading = rid

        def work():
            try:
                self.core.player.play(rid, side, track)
            except Exception as e:
                msg = str(e).strip("'\"")
                self.ui.put(lambda: messagebox.showerror("Vinyl Presence", msg, parent=self.root))
            finally:
                self.ui.put(lambda: setattr(self, "loading", None))
        threading.Thread(target=work, daemon=True).start()

    def play_track_row(self, iid):
        snap = self.core.player.snapshot()
        if not snap or not iid:
            return
        kind, side, *rest = iid.split("|")
        self.play(snap["record"]["id"], side, int(rest[0]) if kind == "t" else None)

    # ------------------------------------------------------------ refresh loop

    def _loop(self):
        try:
            while True:
                self.ui.get_nowait()()
        except queue.Empty:
            pass
        snap = self.core.player.snapshot()
        sig = (self.loading, snap and (snap["record"]["id"], snap["side"], snap["idx"], snap["side_done"],
                                       snap["cover"], len(snap["tracks"])))
        if sig != self.now_sig:
            self.now_sig = sig
            self.render_now(snap)
        self.render_progress(snap)
        if self.ticks % 5 == 0:
            self.render_status()
        self.ticks += 1
        self.root.after(200, self._loop)

    def _set_playing_row(self, iid):
        if iid == self.playing_iid:
            return
        for old, add in ((self.playing_iid, False), (iid, True)):
            if old and self.tree.exists(old):
                tags = [t for t in self.tree.item(old, "tags") if t != "playing"] + (["playing"] if add else [])
                self.tree.item(old, tags=tags)
        self.playing_iid = iid

    def render_now(self, snap):
        for w in self.sides_frame.winfo_children():
            w.destroy()
        self.tracks.delete(*self.tracks.get_children())
        if self.loading and not (snap and snap["record"]["id"] == self.loading):
            rec = self.core.records.get(str(self.loading)) or {}
            self.eyebrow.config(text="DROPPING THE NEEDLE…")
            self.title_lbl.config(text=rec.get("title", ""))
            self.sub_lbl.config(text=rec.get("artist", ""))
            self.show_cover(None)
            self._controls(False)
            self._tracks_message("Looking up the tracklist…")
            return
        if not snap:
            self._set_playing_row(None)
            self.eyebrow.config(text="NOTHING ON THE PLATTER")
            self.title_lbl.config(text="Pick a record")
            self.sub_lbl.config(text="Type a few letters below and press Enter. Your Discord status follows.")
            self.show_cover(None)
            self._controls(False)
            self._tracks_message("The tracklist shows up here\nonce a record is playing.")
            return

        rec, side, idx = snap["record"], snap["side"], snap["idx"]
        self._set_playing_row(str(rec["id"]))
        self.show_cover(snap["cover"], rec["id"])
        tracks = snap["tracks"]
        if snap["side_done"]:
            nxt = snap["next_side"]
            self.eyebrow.config(text=f"{side_name(side).upper()} FINISHED")
            self.title_lbl.config(text=rec["title"])
            self.sub_lbl.config(text=rec["artist"])
            if nxt:
                self.banner_lbl.config(text="Flip the record?")
                self.banner_btn.config(text=f"▶  Play {side_name(nxt)}")
                self.banner_btn.bind("<Button-1>", lambda e: self.play(rec["id"], nxt))
            else:
                self.banner_lbl.config(text="That's the end of the record.")
                self.banner_btn.config(text="■  Clear status")
                self.banner_btn.bind("<Button-1>", lambda e: self.bg(self.core.player.control, "stop"))
        elif idx is not None:
            tr = tracks[idx]
            parts = ["NOW SPINNING", side_name(side).upper(), f"TRACK {idx + 1} OF {len(tracks)}"]
            self.eyebrow.config(text=" · ".join(p for p in parts if p))
            self.title_lbl.config(text=tr["title"])
            self.sub_lbl.config(text=f"{tr.get('artist') or rec['artist']}  —  {rec['title']}")
        else:
            self.eyebrow.config(text=" · ".join(p for p in ("NOW SPINNING", side_name(side).upper()) if p))
            self.title_lbl.config(text=rec["title"])
            extra = " · ".join(x for x in (rec["year"], rec["label"]) if x)
            self.sub_lbl.config(text=f"{rec['artist']}  ·  {extra}" if extra else rec["artist"])

        if len(snap["sides"]) > 1:
            tk.Label(self.sides_frame, text="Side", bg=PANEL, fg=FAINT, font=(FONT, 9)).pack(side="left",
                                                                                           padx=(0, self.px(6)))
            for s in snap["sides"]:
                b = self.btn(self.sides_frame, s, lambda s=s: self.play(rec["id"], s),
                             kind="accent" if s == side else "normal")
                b.pack(side="left", padx=(0, self.px(4)))
        self._controls(True, idx is not None)

        # full tracklist, grouped per side
        self.tracks_msg.place_forget()
        info = self.core.library.get_cached(rec["id"]) or {}
        all_tracks = info.get("tracks") or []
        if not all_tracks:
            self._tracks_message("No tracklist found for this release.\nThe status still shows the album.")
        last_side = None
        side_counter = {}
        for t in all_tracks:
            s = t["side"]
            n = side_counter.get(s, 0)
            side_counter[s] = n + 1
            if s != last_side:
                same_side = [x for x in all_tracks if x["side"] == s]
                total = sum(x["duration"] or 0 for x in same_side)
                exact = all(x["duration"] for x in same_side)
                self.tracks.insert("", "end", iid=f"s|{s}", tags=("side",),
                                   values=("", side_name(s).upper(), fmt_time(total) if exact else ""))
                last_side = s
            tags = ()
            if s == side and not snap["side_done"] and idx is not None:
                tags = ("current",) if n == idx else (("done",) if n < idx else ())
            elif s == side and snap["side_done"]:
                tags = ("done",)
            self.tracks.insert("", "end", iid=f"t|{s}|{n}", tags=tags,
                               values=(t["pos"], t["title"], fmt_time(t["duration"]) if t["duration"] else ""))
        cur = f"t|{side}|{idx}" if idx is not None else f"s|{side}"
        if self.tracks.exists(cur):
            self.tracks.see(cur)

    def _controls(self, show, track_mode=False):
        for w in (self.prev_btn, self.next_btn):
            w.grid() if show and track_mode else w.grid_remove()
        self.stop_btn.grid() if show else self.stop_btn.grid_remove()

    def _tracks_message(self, text):
        self.tracks_msg.config(text=text)
        self.tracks_msg.place(relx=0.5, rely=0.4, anchor="center")

    def render_progress(self, snap):
        if not snap or self.loading:
            self.prog.grid_remove()
            self.banner.grid_remove()
            return
        if snap["side_done"]:
            self.prog.grid_remove()
            self.banner.grid(row=3, column=0, sticky="w", pady=(self.px(10), 0))
            return
        self.banner.grid_remove()
        self.prog.grid()
        now = time.time() * 1000
        frac, end = None, ""
        if snap["idx"] is not None:
            dur = snap["tracks"][snap["idx"]]["duration"]
            el = (now - snap["track_started"]) / 1000
            if dur:
                el = min(el, dur)
                frac, end = el / dur, fmt_time(dur)
        else:
            el = (now - (snap["track_started"] if snap["side"] else snap["started"])) / 1000
            end = "elapsed"
        self.t_cur.config(text=fmt_time(el))
        self.t_end.config(text=end)
        w, h = self.bar.winfo_width(), self.px(6)
        self.bar.delete("all")
        r = h / 2
        self.bar.create_line(r, r, w - r, r, width=h, capstyle="round", fill=LINE)
        if frac:
            self.bar.create_line(r, r, max(r, r + (w - h) * frac), r, width=h, capstyle="round", fill=ACCENT)

    def render_status(self):
        st = self.core.discord.status
        color, text = DISCORD_STATES.get(st["state"], (WARN, st["state"]))
        if st["state"] == "connected" and st.get("user"):
            text = f"Discord connected · {st['user']}"
        if st.get("error") and st["state"] == "connected":
            color, text = WARN, f"Discord: {st['error']}"
        self.dc_dot.config(fg=color)
        self.dc_text.config(text=text)
        pending = self.core.library.pending()
        self.meta_lbl.config(text=f"Looking up covers & track lengths… {pending} to go" if pending else "")

    # ------------------------------------------------------------ covers

    def show_cover(self, url, rid=None):
        self.cover_url = url
        if not url or not Image:
            self.cover_lbl.config(image=self.vinyl_cover)
            return
        if url in self.covers:
            self.cover_lbl.config(image=self.covers[url])
            return
        self.cover_lbl.config(image=self.vinyl_cover)
        size = self.cover_size

        def work():
            path = DATA / "covers" / (hashlib.md5(url.encode()).hexdigest()[:16] + ".jpg")
            try:
                if path.exists():
                    data = path.read_bytes()
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read()
                    path.parent.mkdir(exist_ok=True)
                    path.write_bytes(data)
                img = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (size, size), Image.LANCZOS)
                mask = Image.new("L", (size, size), 0)
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=self.px(8), fill=255)
                framed = Image.new("RGB", (size, size), PANEL)
                framed.paste(img, (0, 0), mask)
            except Exception as e:
                print(f"[cover] {url}: {e}")
                return
            self.ui.put(lambda: self._cover_ready(url, framed))

        threading.Thread(target=work, daemon=True).start()

    def _cover_ready(self, url, img):
        self.covers[url] = ImageTk.PhotoImage(img)
        if self.cover_url == url:
            self.cover_lbl.config(image=self.covers[url])

    # ------------------------------------------------------------ collection & settings

    def import_csv(self, parent=None):
        path = filedialog.askopenfilename(parent=parent or self.root, title="Choose your Discogs collection export",
                                          filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return False
        try:
            count = self.core.import_csv(Path(path).read_text("utf-8-sig", errors="replace"))
        except (OSError, ValueError) as e:
            messagebox.showerror("Import failed", str(e), parent=parent or self.root)
            return False
        self.refresh_list()
        self.count_lbl.config(text=f"Imported {count} records. Covers and track lengths load in the background.")
        return True

    def open_settings(self):
        if self.settings and self.settings.winfo_exists():
            self.settings.lift()
            return
        px, cfg = self.px, self.core.config
        w = self.settings = tk.Toplevel(self.root, bg=BG)
        w.title("Vinyl Presence settings")
        w.transient(self.root)
        w.resizable(False, False)
        w.bind("<Escape>", lambda e: w.destroy())
        dark_titlebar(w)
        pad = tk.Frame(w, bg=BG)
        pad.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        guide = tk.Frame(pad, bg=PANEL)
        guide.pack(fill="x")
        g = tk.Frame(guide, bg=PANEL)
        g.pack(fill="x", padx=px(16), pady=px(14))
        tk.Label(g, text="Connect to Discord (one time, about 2 minutes)", bg=PANEL, fg=TEXT,
                 font=(FONT, 11, "bold"), anchor="w").pack(fill="x")
        steps = [
            "1.  Open the Discord Developer Portal and click New Application. Its name is the title of your "
            "status (\"Listening to My Record Player\"), so make it say record player or turntable.",
            "2.  On General Information, copy the Application ID and paste it below.",
            "3.  Recommended: under Rich Presence → Art Assets → Rich Presence Assets, click Add Image(s), upload "
            "vinyl.png (Show vinyl.png, below) and name it vinyl. It puts a vinyl badge on the album cover.",
            "4.  In Discord: User Settings → Activity Privacy → turn on \"Share your detected activities\".",
        ]
        for s in steps:
            tk.Label(g, text=s, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                     wraplength=px(520)).pack(fill="x", pady=(px(4), 0))
        links = tk.Frame(g, bg=PANEL)
        links.pack(fill="x", pady=(px(8), 0))
        self.link(links, "Open Discord Developer Portal ↗", "https://discord.com/developers/applications").pack(side="left")
        self.link(links, "Stuck? Ask in our Discord ↗", SUPPORT_DISCORD).pack(side="right")
        badge = tk.Label(links, text="Show vinyl.png", bg=PANEL, fg=ACCENT, cursor="hand2",
                         font=(FONT, 9, "underline"))
        badge.bind("<Button-1>", lambda e: self.reveal_badge())
        badge.pack(side="left", padx=px(16))

        form = tk.Frame(pad, bg=BG)
        form.pack(fill="x", pady=(px(16), 0))
        form.columnconfigure(1, weight=1)
        row = 0

        def label(text, hint=None, hint_color=FAINT):
            nonlocal row
            tk.Label(form, text=text, bg=BG, fg=TEXT, font=(FONT, 10), anchor="w").grid(
                row=row, column=0, sticky="w", pady=px(5), padx=(0, px(14)))
            if hint:
                tk.Label(form, text=hint, bg=BG, fg=hint_color, font=(FONT, 8), anchor="w").grid(
                    row=row + 1, column=1, sticky="w")

        v_client = tk.StringVar(value=cfg["discord_client_id"])
        label("Discord Application ID")
        self.make_entry(form, v_client).grid(row=row, column=1, sticky="ew", ipady=px(3))
        row += 1

        v_card = tk.StringVar(value=CARD_TITLES.get(cfg["card_title"], CARD_TITLES["app"]))
        v_card_text = tk.StringVar(value=cfg["card_title_text"])
        ignored = self.core.discord.status.get("name_ignored")
        label("Card title", "Your Discord ignores this setting: rename the app in the Developer Portal instead."
              if ignored else "The top line of your status card.", WARN if ignored else FAINT)
        card = tk.Frame(form, bg=BG)
        card.grid(row=row, column=1, sticky="ew")
        card_box = ttk.Combobox(card, textvariable=v_card, values=list(CARD_TITLES.values()), state="readonly",
                                width=26, font=(FONT, 10))
        card_box.pack(side="left")
        card_text = self.make_entry(card, v_card_text, width=16)
        card_text.pack(side="left", padx=(px(8), 0), ipady=px(3))

        def toggle_card_text(_e=None):
            card_text.config(state="normal" if v_card.get() == CARD_TITLES["custom"] else "disabled",
                             disabledbackground=BG, disabledforeground=FAINT)
        card_box.bind("<<ComboboxSelected>>", toggle_card_text)
        toggle_card_text()
        row += 2

        v_status = tk.StringVar(value=STATUS_CHOICES.get(cfg["status_display"], STATUS_CHOICES["artist"]))
        label("Member list shows")
        ttk.Combobox(form, textvariable=v_status, values=list(STATUS_CHOICES.values()), state="readonly",
                     font=(FONT, 10)).grid(row=row, column=1, sticky="ew")
        row += 1

        v_button = tk.BooleanVar(value=cfg["show_discogs_button"])
        tk.Checkbutton(form, text="Show a \"View on Discogs\" button on my status", variable=v_button,
                       bg=BG, fg=TEXT, selectcolor=PANEL2, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10), anchor="w").grid(row=row, column=1, sticky="w", pady=px(4))
        row += 1

        v_asset = tk.StringVar(value=cfg["vinyl_asset"])
        assets = self.core.app_assets
        missing = assets is not None and cfg["vinyl_asset"].strip() not in assets
        label("Vinyl badge asset name",
              "Not uploaded to your Discord app yet: see step 3. The badge appears once it is." if missing
              else "The art asset name from step 3.", WARN if missing else FAINT)
        self.make_entry(form, v_asset, width=16).grid(row=row, column=1, sticky="w", ipady=px(3))
        row += 2

        tk.Frame(form, bg=LINE, height=1).grid(row=row, column=0, columnspan=2, sticky="ew", pady=px(12))
        row += 1

        v_token = tk.StringVar(value=cfg["discogs_token"])
        label("Discogs token (optional)", "Makes looking up covers & tracklists faster.")
        tok = tk.Frame(form, bg=BG)
        tok.grid(row=row, column=1, sticky="ew")
        self.make_entry(tok, v_token, show="•", width=28).pack(side="left", ipady=px(3))
        self.link(tok, "Get one ↗", "https://www.discogs.com/settings/developers", bg=BG).pack(side="left", padx=px(10))
        row += 2

        v_auto = tk.BooleanVar(value=cfg["auto_continue"])
        v_flip = tk.StringVar(value=str(cfg["flip_seconds"]))
        flip = tk.Frame(form, bg=BG)
        flip.grid(row=row, column=1, sticky="w", pady=px(6))
        tk.Checkbutton(flip, text="When a side ends, continue with the next side after", variable=v_auto,
                       bg=BG, fg=TEXT, selectcolor=PANEL2, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10)).pack(side="left")
        tk.Spinbox(flip, from_=0, to=600, increment=5, textvariable=v_flip, width=4, bg=PANEL2, fg=TEXT,
                   buttonbackground=PANEL2, insertbackground=TEXT, relief="flat", font=(FONT, 10)).pack(side="left")
        tk.Label(flip, text="sec", bg=BG, fg=TEXT, font=(FONT, 10)).pack(side="left", padx=px(4))
        row += 1

        label("Collection")
        col = tk.Frame(form, bg=BG)
        col.grid(row=row, column=1, sticky="ew")
        col_lbl = tk.Label(col, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w",
                           text=f"{len(self.core.records)} records · {self.core.collection_file}"
                           if self.core.records else "No collection imported yet")
        col_lbl.pack(side="left")

        def do_import():
            if self.import_csv(parent=w):
                col_lbl.config(text=f"{len(self.core.records)} records · {self.core.collection_file}")
        self.btn(col, "Import Discogs CSV…", do_import).pack(side="right")
        row += 1

        def save():
            client = v_client.get().strip()
            if client and not client.isdigit():
                messagebox.showerror("Settings", "The Application ID is a long number, e.g. 1234567890123456789.",
                                     parent=w)
                return
            try:
                flip_s = max(0, int(v_flip.get()))
            except ValueError:
                flip_s = 30
            status_key = next(k for k, v in STATUS_CHOICES.items() if v == v_status.get())
            card_key = next(k for k, v in CARD_TITLES.items() if v == v_card.get())
            self.core.update_config({
                "discord_client_id": client, "status_display": status_key,
                "card_title": card_key, "card_title_text": v_card_text.get().strip() or "Vinyl",
                "show_discogs_button": v_button.get(), "vinyl_asset": v_asset.get().strip(),
                "discogs_token": v_token.get().strip(), "auto_continue": v_auto.get(), "flip_seconds": flip_s,
            })
            w.destroy()

        bar = tk.Frame(pad, bg=BG)
        bar.pack(fill="x", pady=(px(18), 0))
        tk.Label(bar, text=f"Vinyl Presence {VERSION}", bg=BG, fg=FAINT, font=(FONT, 8)).pack(side="left")
        self.btn(bar, "Save", save, kind="accent").pack(side="right")
        self.btn(bar, "Cancel", w.destroy, kind="ghost").pack(side="right", padx=px(8))

        w.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - w.winfo_width()) // 2
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - w.winfo_height()) // 3)
        w.geometry(f"+{max(0, x)}+{max(0, y)}")
        w.grab_set()

    def reveal_badge(self):
        """Show vinyl.png in Explorer so it can be uploaded to the Discord app."""
        path = ASSETS / "vinyl.png"
        if getattr(sys, "frozen", False):  # the bundled copy lives in a temp folder; put one next to the data
            path = ROOT / "vinyl.png"
            if not path.exists():
                shutil.copyfile(ASSETS / "vinyl.png", path)
        if sys.platform == "win32":
            subprocess.Popen(f'explorer /select,"{path}"')

    # ------------------------------------------------------------ updates

    def _check_update(self):
        if not GITHUB_REPO:
            return
        req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                release = json.load(resp)
        except (OSError, ValueError):
            return
        latest = release.get("tag_name") or ""
        if version_tuple(latest) > version_tuple(VERSION):
            print(f"[update] {latest} is available (running {VERSION})")
            self.ui.put(lambda: self._show_update(latest, release.get("html_url")))

    def _show_update(self, latest, url):
        self.update_lbl.config(text=f"Update available: {latest} ↗")
        self.update_lbl.bind("<Button-1>", lambda e: webbrowser.open(url))
        self.update_lbl.pack(side="right", padx=(0, self.px(4)))

    def close(self):
        self.root.withdraw()
        self.core.shutdown()
        self.root.destroy()


# ---------------------------------------------------------------- startup

def dark_titlebar(window):
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(ctypes.c_int(1)), 4)
    except (AttributeError, OSError):
        pass


def single_instance_lock():
    f = open(DATA / ".lock", "a+")
    try:
        f.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if sys.stdout is None:  # started with pythonw: keep a log instead of a console
        sys.stdout = sys.stderr = open(DATA / "log.txt", "a", encoding="utf-8", buffering=1)
    else:  # a Windows console can't print every artist name ("33⅓", "Sigur Rós", ...)
        for stream in (sys.stdout, sys.stderr):
            stream.reconfigure(errors="replace")
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    lock = single_instance_lock()
    root = tk.Tk()
    if not lock:
        root.withdraw()
        messagebox.showinfo("Vinyl Presence", "Vinyl Presence is already open. Check your taskbar.")
        return
    root.title("Vinyl Presence")
    root.configure(bg=BG)
    scale = root.winfo_fpixels("1i") / 96
    root.geometry(f"{int(1120 * scale)}x{int(760 * scale)}")
    root.minsize(int(860 * scale), int(600 * scale))
    root.iconphoto(True, vinyl_image(64))
    dark_titlebar(root)
    core = Core()
    app = VinylApp(root, core)
    root.protocol("WM_DELETE_WINDOW", app.close)
    root.mainloop()


if __name__ == "__main__":
    main()
