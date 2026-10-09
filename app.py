"""Vinyl Presence: show the record on your turntable as Discord Rich Presence.

A normal desktop window, nothing is hosted. Double-click start.bat (or run
`pythonw app.py`), type a few letters of the record, press Enter.
"""
import ctypes
import hashlib
import io
import queue
import random
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

import server_bot
import share
import tray
import updater
from core import ASSETS, DATA, ROOT, Core, search
from covers import CoverGrid
from metadata import USER_AGENT
from stats_view import StatsWindow
from theme import ACCENT, ACCENT_DIM, ACCENT_HI, BAD, BG, FAINT, FONT, GOOD, LINE, MUTED, PANEL, PANEL2, ROW_ALT, TEXT, WARN
from version import VERSION

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:  # covers in the window need Pillow; everything else works without it
    Image = None

SUPPORT_DISCORD = "https://discord.gg/xHj9Td7MYz"  # Vinyl Hangout: questions & help
SHOW_REQUEST = DATA / "show.request"  # a second start drops this file to bring the window back
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
        self.stats_win = None
        self.tray = None
        self.items = []
        self.meta_version = -1
        self.ticks = 0

        self._style()
        self._build()
        self.refresh_list()
        self.entry.focus_set()
        self.root.after(150, self._loop)
        self.update = None
        self.updating = False
        self.closed = False
        self._start_tray()
        threading.Thread(target=self._check_update, daemon=True, name="update-check").start()
        threading.Thread(target=core.player.restore_state, daemon=True, name="resume").start()
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
        st.configure("Dark.TNotebook", background=BG, bordercolor=LINE, lightcolor=BG, darkcolor=BG,
                     tabmargins=(0, 0, 0, 0))
        st.configure("Dark.TNotebook.Tab", background=BG, foreground=MUTED, font=(FONT, 10),
                     padding=(self.px(12), self.px(6)), bordercolor=LINE, lightcolor=BG, darkcolor=BG, focuscolor=BG)
        st.map("Dark.TNotebook.Tab", background=[("selected", PANEL2), ("active", PANEL)],
               foreground=[("selected", TEXT), ("active", TEXT)], lightcolor=[("selected", PANEL2)])

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
        self.btn(top, "Stats", self.open_stats, kind="ghost").pack(side="right", padx=(0, px(6)))
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
        # nudge the timer when play was clicked a bit before/after the needle dropped
        self.back_btn = self.btn(ctl, "−10s", lambda: self.core.player.nudge(-10), kind="ghost", size=9)
        self.back_btn.grid(row=0, column=2, padx=(0, px(2)))
        self.fwd_btn = self.btn(ctl, "+10s", lambda: self.core.player.nudge(10), kind="ghost", size=9)
        self.fwd_btn.grid(row=0, column=3, padx=(0, px(14)))
        self.prev_btn = self.btn(ctl, "⏮", lambda: self.bg(self.core.player.control, "prev"), size=11)
        self.prev_btn.grid(row=0, column=4, padx=(0, px(6)))
        self.next_btn = self.btn(ctl, "⏭", lambda: self.bg(self.core.player.control, "next"), size=11)
        self.next_btn.grid(row=0, column=5, padx=(0, px(6)))
        self.stop_btn = self.btn(ctl, "■  Stop", lambda: self.bg(self.core.player.control, "stop"))
        self.stop_btn.grid(row=0, column=6)

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
        views = tk.Frame(mr, bg=PANEL)
        views.pack(side="right", padx=(0, px(16)))
        self.view_btns = {}
        for key, label in (("list", "☰  List"), ("covers", "⊞  Covers")):
            b = self.btn(views, label, lambda key=key: self.set_view(key), size=9)
            b.pack(side="left", padx=(0, px(2)))
            self.view_btns[key] = b

        tf = tk.Frame(lp, bg=PANEL)
        tf.pack(fill="both", expand=True)
        self.list_frame = tk.Frame(tf, bg=PANEL)
        self.grid_view = CoverGrid(tf, self, on_play=self.play)
        cols = (("artist", "ARTIST", 200), ("title", "TITLE", 260), ("year", "YEAR", 56), ("format", "FORMAT", 150))
        self.tree = ttk.Treeview(self.list_frame, columns=[c[0] for c in cols], show="headings", selectmode="browse")
        for key, label, width in cols:
            self.tree.heading(key, text=label, anchor="w")
            self.tree.column(key, width=px(width), minwidth=px(40), stretch=key in ("artist", "title", "format"))
        self.tree.tag_configure("odd", background=ROW_ALT)
        self.tree.tag_configure("playing", foreground=ACCENT_HI)
        sb = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.view = self.core.config.get("list_view") if self.core.config.get("list_view") in self.view_btns else "list"
        self._show_view()

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
        self.items = items
        if self.view == "covers":
            self.grid_view.set_items(items, select_first=bool(query))
        else:
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

    def set_view(self, view):
        if view != self.view:
            self.view = view
            self.core.update_config({"list_view": view})
            self._show_view()
            self.refresh_list()
        self.entry.focus_set()

    def _show_view(self):
        if self.view == "covers":
            self.list_frame.pack_forget()
            self.grid_view.pack(fill="both", expand=True)
        else:
            self.grid_view.pack_forget()
            self.list_frame.pack(fill="both", expand=True)
        for key, b in self.view_btns.items():
            b.base_bg = ACCENT_DIM if key == self.view else PANEL2
            b.config(bg=b.base_bg, fg=TEXT if key == self.view else MUTED)

    def move_selection(self, delta):
        if self.view == "covers":
            self.grid_view.move(delta if abs(delta) == 1 else (3 if delta > 0 else -3))
            return "break"
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
        if not self.items:
            return
        pick = random.choice(self.items)["id"]
        if self.view == "covers":
            self.grid_view.select_id(pick)
        else:
            self.tree.selection_set(str(pick))
            self.tree.see(str(pick))
        self.entry.focus_set()
        self.count_lbl.config(text="How about this one? Press Enter to play it.")

    def play_selected(self):
        if self.view == "covers":
            rid = self.grid_view.selected_id()
        else:
            sel = self.tree.selection()
            rid = int(sel[0]) if sel else None
        if rid is not None:
            self.play(rid)
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
            while not self.closed:
                self.ui.get_nowait()()
        except queue.Empty:
            pass
        if self.closed:  # e.g. the updater just closed the window
            return
        snap = self.core.player.snapshot()
        sig = (self.loading, snap and (snap["record"]["id"], snap["side"], snap["idx"], snap["side_done"],
                                       snap["cover"], len(snap["tracks"])))
        if sig != self.now_sig:
            self.now_sig = sig
            self.render_now(snap)
        self.render_progress(snap)
        if self.ticks % 5 == 0:
            self.render_status()
            if SHOW_REQUEST.exists():  # someone started the app again while it's in the tray
                SHOW_REQUEST.unlink(missing_ok=True)
                self.show_window()
            if self.view == "covers" and self.core.library.version != self.meta_version:
                self.meta_version = self.core.library.version
                self.grid_view.refresh_missing()  # covers whose lookup just finished
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
        self.grid_view.set_playing(int(iid) if iid else None)
        if self.tray:
            self.tray.update(f"Vinyl Presence · {self._tray_now_text()}")

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
        self._controls(True, idx is not None, nudge=not snap["side_done"])

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

    def _controls(self, show, track_mode=False, nudge=False):
        for w in (self.prev_btn, self.next_btn):
            w.grid() if show and track_mode else w.grid_remove()
        for w in (self.back_btn, self.fwd_btn):
            w.grid() if show and nudge else w.grid_remove()
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
        w.bind("<Escape>", lambda e: close_settings())
        w.protocol("WM_DELETE_WINDOW", lambda: close_settings())
        dark_titlebar(w)
        pad = tk.Frame(w, bg=BG)
        pad.pack(fill="both", expand=True, padx=px(22), pady=px(18))
        tabs = ttk.Notebook(pad, style="Dark.TNotebook")
        tabs.pack(fill="both", expand=True)

        def tab(title):
            outer = tk.Frame(tabs, bg=BG)
            tabs.add(outer, text=f"  {title}  ")
            form = tk.Frame(outer, bg=BG)
            form.pack(fill="both", expand=True, padx=px(4), pady=(px(14), 0))
            form.columnconfigure(1, weight=1)
            form.row = 0
            return form

        def label(form, text, hint=None, hint_color=FAINT):
            tk.Label(form, text=text, bg=BG, fg=TEXT, font=(FONT, 10), anchor="w").grid(
                row=form.row, column=0, sticky="w", pady=px(5), padx=(0, px(14)))
            if hint:
                tk.Label(form, text=hint, bg=BG, fg=hint_color, font=(FONT, 8), anchor="w", justify="left",
                         wraplength=px(400)).grid(row=form.row + 1, column=1, sticky="w")

        def check(form, text, var, column=1):
            cb = tk.Checkbutton(form, text=text, variable=var, bg=BG, fg=TEXT, selectcolor=PANEL2,
                                activebackground=BG, activeforeground=TEXT, disabledforeground=FAINT,
                                font=(FONT, 10), anchor="w")
            cb.grid(row=form.row, column=column, columnspan=2 if column == 0 else 1, sticky="w", pady=px(3))
            return cb

        # ---- Discord
        f = tab("Discord")
        guide = tk.Frame(f, bg=PANEL)
        guide.grid(row=f.row, column=0, columnspan=2, sticky="ew", pady=(0, px(12)))
        f.row += 1
        g = tk.Frame(guide, bg=PANEL)
        g.pack(fill="x", padx=px(16), pady=px(12))
        head = tk.Frame(g, bg=PANEL)
        head.pack(fill="x")
        tk.Label(head, text="Connect to Discord (one time, about 2 minutes)", bg=PANEL, fg=TEXT,
                 font=(FONT, 11, "bold"), anchor="w").pack(side="left")
        steps_box = tk.Frame(g, bg=PANEL)
        steps = [
            "1.  Open the Discord Developer Portal and click New Application. Its name is the title of your "
            "status (\"Listening to My Record Player\"), so make it say record player or turntable.",
            "2.  On General Information, copy the Application ID and paste it below.",
            "3.  Recommended: under Rich Presence → Art Assets → Rich Presence Assets, click Add Image(s), upload "
            "vinyl.png (Show vinyl.png, below) and name it vinyl. It puts a vinyl badge on the album cover.",
            "4.  In Discord: User Settings → Activity Privacy → turn on \"Share your detected activities\".",
        ]
        for s in steps:
            tk.Label(steps_box, text=s, bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                     wraplength=px(520)).pack(fill="x", pady=(px(4), 0))
        links = tk.Frame(steps_box, bg=PANEL)
        links.pack(fill="x", pady=(px(8), 0))
        self.link(links, "Open Discord Developer Portal ↗", "https://discord.com/developers/applications").pack(side="left")
        badge = tk.Label(links, text="Show vinyl.png", bg=PANEL, fg=ACCENT, cursor="hand2",
                         font=(FONT, 9, "underline"))
        badge.bind("<Button-1>", lambda e: self.reveal_badge())
        badge.pack(side="left", padx=px(16))
        self.link(links, "Stuck? Ask in our Discord ↗", SUPPORT_DISCORD).pack(side="right")

        def toggle_steps(_e=None):
            if steps_box.winfo_ismapped():
                steps_box.pack_forget()
                toggle.config(text="Show steps ▾")
            else:
                steps_box.pack(fill="x")
                toggle.config(text="Hide steps ▴")
        toggle = tk.Label(head, bg=PANEL, fg=ACCENT, cursor="hand2", font=(FONT, 9))
        toggle.bind("<Button-1>", toggle_steps)
        toggle.pack(side="right")
        if cfg["discord_client_id"]:  # already set up: keep the guide folded away
            toggle.config(text="Show steps ▾")
        else:
            toggle_steps()

        v_client = tk.StringVar(value=cfg["discord_client_id"])
        label(f, "Discord Application ID")
        self.make_entry(f, v_client).grid(row=f.row, column=1, sticky="ew", ipady=px(3))
        f.row += 1

        v_card = tk.StringVar(value=CARD_TITLES.get(cfg["card_title"], CARD_TITLES["app"]))
        v_card_text = tk.StringVar(value=cfg["card_title_text"])
        ignored = self.core.discord.status.get("name_ignored")
        label(f, "Card title", "Your Discord ignores this setting: rename the app in the Developer Portal instead."
              if ignored else "The top line of your status card.", WARN if ignored else FAINT)
        card = tk.Frame(f, bg=BG)
        card.grid(row=f.row, column=1, sticky="ew")
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
        f.row += 2

        v_status = tk.StringVar(value=STATUS_CHOICES.get(cfg["status_display"], STATUS_CHOICES["artist"]))
        label(f, "Member list shows")
        ttk.Combobox(f, textvariable=v_status, values=list(STATUS_CHOICES.values()), state="readonly",
                     font=(FONT, 10)).grid(row=f.row, column=1, sticky="ew")
        f.row += 1

        v_button = tk.BooleanVar(value=cfg["show_discogs_button"])
        check(f, "Show a \"View on Discogs\" button on my status", v_button)
        f.row += 1

        v_asset = tk.StringVar(value=cfg["vinyl_asset"])
        assets = self.core.app_assets
        missing = assets is not None and cfg["vinyl_asset"].strip() not in assets
        label(f, "Vinyl badge asset name",
              "Not uploaded to your Discord app yet: see step 3. The badge appears once it is." if missing
              else "The art asset name from step 3.", WARN if missing else FAINT)
        self.make_entry(f, v_asset, width=16).grid(row=f.row, column=1, sticky="w", ipady=px(3))
        f.row += 2

        # ---- Records
        f = tab("Records")
        label(f, "Collection")
        col = tk.Frame(f, bg=BG)
        col.grid(row=f.row, column=1, sticky="ew")
        col_lbl = tk.Label(col, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w",
                           text=f"{len(self.core.records)} records · {self.core.collection_file}"
                           if self.core.records else "No collection imported yet")
        col_lbl.pack(side="left")

        def do_import():
            if self.import_csv(parent=w):
                col_lbl.config(text=f"{len(self.core.records)} records · {self.core.collection_file}")
        self.btn(col, "Import Discogs CSV…", do_import).pack(side="right")
        f.row += 1

        v_token = tk.StringVar(value=cfg["discogs_token"])
        label(f, "Discogs token (optional)", "Makes looking up covers & tracklists faster.")
        tok = tk.Frame(f, bg=BG)
        tok.grid(row=f.row, column=1, sticky="ew")
        self.make_entry(tok, v_token, show="•", width=28).pack(side="left", ipady=px(3))
        self.link(tok, "Get one ↗", "https://www.discogs.com/settings/developers", bg=BG).pack(side="left", padx=px(10))
        f.row += 2

        v_auto = tk.BooleanVar(value=cfg["auto_continue"])
        v_flip = tk.StringVar(value=str(cfg["flip_seconds"]))
        flip = tk.Frame(f, bg=BG)
        flip.grid(row=f.row, column=0, columnspan=2, sticky="w", pady=px(10))
        tk.Checkbutton(flip, text="When a side ends, continue with the next side after", variable=v_auto,
                       bg=BG, fg=TEXT, selectcolor=PANEL2, activebackground=BG, activeforeground=TEXT,
                       font=(FONT, 10)).pack(side="left")
        tk.Spinbox(flip, from_=0, to=600, increment=5, textvariable=v_flip, width=4, bg=PANEL2, fg=TEXT,
                   buttonbackground=PANEL2, insertbackground=TEXT, relief="flat", font=(FONT, 10)).pack(side="left")
        tk.Label(flip, text="sec", bg=BG, fg=TEXT, font=(FONT, 10)).pack(side="left", padx=px(4))
        f.row += 1

        # ---- Sharing & tray
        f = tab("Sharing & tray")
        tk.Label(f, text="Now spinning channel", bg=BG, fg=TEXT, font=(FONT, 11, "bold"), anchor="w").grid(
            row=f.row, column=0, columnspan=2, sticky="w")
        f.row += 1
        tk.Label(f, text="Post the records you play to a channel in a Discord server, like #now-spinning. "
                         "A server admin makes a webhook for that channel (Edit Channel → Integrations → "
                         "Webhooks → New Webhook → Copy Webhook URL). A record is posted after it has played "
                         "for 30 seconds.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=px(540)).grid(
            row=f.row, column=0, columnspan=2, sticky="w", pady=(px(2), px(6)))
        f.row += 1
        v_share = tk.BooleanVar(value=cfg["share_enabled"])
        check(f, "Post what I play", v_share, column=0)
        f.row += 1
        v_hook = tk.StringVar(value=cfg["share_webhook"])
        label(f, "Webhook URL", "Keep it private: anyone who has this URL can post in that channel.")
        hook = tk.Frame(f, bg=BG)
        hook.grid(row=f.row, column=1, sticky="ew")
        self.make_entry(hook, v_hook, show="•", width=30).pack(side="left", fill="x", expand=True, ipady=px(3))

        def send_test():
            url = v_hook.get().strip()
            if not share.valid_webhook(url):
                messagebox.showerror("Now spinning", "Paste a Discord webhook URL first. It starts with "
                                     "https://discord.com/api/webhooks/", parent=w)
                return

            def test_ok():
                # It works, so switch posting on and keep the URL right away; no separate Save needed.
                v_share.set(True)
                self.core.update_config({"share_enabled": True, "share_webhook": url})
                baseline[str(v_share)], baseline[str(v_hook)] = True, url
                if w.winfo_exists():
                    messagebox.showinfo("Now spinning", "Test message sent, and posting is now switched on.\n\n"
                                        "Records you play will show up in that channel.", parent=w)

            def work():
                try:
                    share.post_test(url, self.core.discord.status)
                    self.ui.put(test_ok)
                except Exception as e:
                    msg = str(e)
                    self.ui.put(lambda: w.winfo_exists() and messagebox.showerror(
                        "Now spinning", f"That didn't work:\n{msg}", parent=w))
            threading.Thread(target=work, daemon=True).start()
        self.btn(hook, "Send test", send_test).pack(side="left", padx=(px(8), 0))
        f.row += 2

        tk.Frame(f, bg=LINE, height=1).grid(row=f.row, column=0, columnspan=2, sticky="ew", pady=px(14))
        f.row += 1
        tk.Label(f, text="Tray & startup", bg=BG, fg=TEXT, font=(FONT, 11, "bold"), anchor="w").grid(
            row=f.row, column=0, columnspan=2, sticky="w")
        f.row += 1
        v_tray = tk.BooleanVar(value=cfg["close_to_tray"])
        tray_cb = check(f, "Closing the window keeps Vinyl Presence running in the tray (next to the clock)",
                        v_tray, column=0)
        f.row += 1
        v_startup = tk.BooleanVar(value=tray.startup_enabled())
        startup_cb = check(f, "Start Vinyl Presence when Windows starts (quietly, in the tray)", v_startup, column=0)
        f.row += 1
        if not self.tray:
            tray_cb.config(state="disabled")
            tk.Label(f, text="The tray icon needs the pystray package (pip install pystray).", bg=BG, fg=FAINT,
                     font=(FONT, 8), anchor="w").grid(row=f.row, column=0, columnspan=2, sticky="w")
            f.row += 1
        if sys.platform != "win32":
            startup_cb.config(state="disabled")

        # ---- Server bot (for server owners)
        f = tab("Server bot")
        tk.Label(f, text="Now-spinning bot for your server", bg=BG, fg=TEXT, font=(FONT, 11, "bold"),
                 anchor="w").grid(row=f.row, column=0, columnspan=2, sticky="w")
        f.row += 1
        tk.Label(f, text="For server owners. While Vinyl Presence is open, a bot posts the records your members "
                         "play in a channel. Members don't need a webhook or any setup: they type /nowspinning on "
                         "in your server, and the bot sees their status.",
                 bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=px(540)).grid(
            row=f.row, column=0, columnspan=2, sticky="w", pady=(px(2), px(6)))
        f.row += 1
        bot_steps = [
            "1.  In the Discord Developer Portal, click New Application (e.g. \"Vinyl Hangout\"), open Bot, "
            "click Reset Token and copy it.",
            "2.  On that same Bot page, switch on Presence Intent and Server Members Intent, then Save.",
            "3.  Paste the token below, tick Run the bot, and click Save.",
            "4.  Click Invite the bot (appears below once it's online) and add it to your server. Use this link, "
            "not the Developer Portal's install link: that one only adds the commands, not the bot.",
            "5.  In your now-spinning channel, type /nowspinning channel. Members type /nowspinning on.",
            "Tip: if you also had Post what I play on (Sharing & tray) for that channel, turn it off and type "
            "/nowspinning on yourself, so your records aren't posted twice.",
        ]
        for s_ in bot_steps:
            tk.Label(f, text=s_, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left",
                     wraplength=px(540)).grid(row=f.row, column=0, columnspan=2, sticky="w", pady=(px(2), 0))
            f.row += 1
        v_bot = tk.BooleanVar(value=cfg["bot_enabled"])
        bot_cb = check(f, "Run the bot while Vinyl Presence is open", v_bot, column=0)
        f.row += 1
        v_bot_token = tk.StringVar(value=cfg["bot_token"])
        label(f, "Bot token", "Keep it private: it's the bot's password. It stays on this PC.")
        self.make_entry(f, v_bot_token, show="•", width=30).grid(row=f.row, column=1, sticky="ew", ipady=px(3))
        f.row += 2
        bot_status = tk.Label(f, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=px(540))
        bot_status.grid(row=f.row, column=0, columnspan=2, sticky="w", pady=(px(10), 0))
        f.row += 1
        bot_invite = tk.Label(f, text="Invite the bot to your server ↗", bg=BG, fg=ACCENT, cursor="hand2",
                              font=(FONT, 9, "underline"))
        bot_invite.bind("<Button-1>", lambda e: self.core.bot.invite_url() and webbrowser.open(self.core.bot.invite_url()))
        invite_row = f.row
        f.row += 1
        if not server_bot.available():
            bot_cb.config(state="disabled")
            bot_status.config(text="The bot needs the discord.py package (pip install discord.py).", fg=FAINT)

        def refresh_bot_status():
            if not w.winfo_exists():
                return
            if server_bot.available():
                d = self.core.bot.details()
                text, color = {
                    "off": ("Bot is off.", FAINT),
                    "connecting": ("Connecting…", WARN),
                    "bad_token": ("Discord rejected the token. Copy it again from the Bot page.", BAD),
                    "intents": ("Switch on Presence Intent and Server Members Intent on the Bot page, then restart "
                                "the bot (untick, Save, tick, Save).", BAD),
                    "error": (f"Bot error: {d.get('error')}", BAD),
                }.get(d["state"], ("", MUTED))
                if d["state"] == "online":
                    if "name" in d and not d["servers"]:
                        text = (f"Online as {d['name']}, but it isn't in any server yet. Add it with Invite the bot "
                                "below. (The Developer Portal's install link only adds the commands, so the bot "
                                "can't see anyone or post.)")
                        color = WARN
                    elif "name" in d:
                        text = (f"● Online as {d['name']} · {d['servers']} server(s) · "
                                f"{'channel set' if d['channels'] else 'no channel yet: type /nowspinning channel'}"
                                f" · {d['members']} member(s) opted in")
                        color = GOOD
                    else:
                        text, color = "Online, getting ready…", GOOD
                bot_status.config(text=text, fg=color)
                if self.core.bot.invite_url():
                    bot_invite.grid(row=invite_row, column=0, columnspan=2, sticky="w", pady=(px(4), 0))
                else:
                    bot_invite.grid_remove()
            w.after(1000, refresh_bot_status)
        refresh_bot_status()

        # What the settings were when the window opened (or last saved), to spot unsaved changes
        watched = [v_client, v_card, v_card_text, v_status, v_button, v_asset, v_token, v_auto, v_flip,
                   v_share, v_hook, v_tray, v_startup, v_bot, v_bot_token]
        baseline = {str(v): v.get() for v in watched}

        def close_settings():
            """✕ or Esc: don't silently throw away changes."""
            if any(v.get() != baseline[str(v)] for v in watched):
                answer = messagebox.askyesnocancel("Settings", "Save your changes?", parent=w)
                if answer is None:
                    return
                if answer:
                    return save()
            w.destroy()

        def save():
            client = v_client.get().strip()
            if client and not client.isdigit():
                tabs.select(0)
                messagebox.showerror("Settings", "The Application ID is a long number, e.g. 1234567890123456789.",
                                     parent=w)
                return
            hook_url = v_hook.get().strip()
            if v_share.get() and not share.valid_webhook(hook_url):
                tabs.select(2)
                messagebox.showerror("Settings", "To post what you play, paste the channel's webhook URL. It starts "
                                     "with https://discord.com/api/webhooks/", parent=w)
                return
            if v_bot.get() and not v_bot_token.get().strip():
                tabs.select(3)
                messagebox.showerror("Settings", "To run the bot, paste its token (Developer Portal → your "
                                     "application → Bot → Reset Token).", parent=w)
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
                "share_enabled": v_share.get(), "share_webhook": hook_url, "close_to_tray": v_tray.get(),
                "bot_enabled": v_bot.get(), "bot_token": v_bot_token.get().strip(),
            })
            if v_startup.get() != tray.startup_enabled():
                try:
                    tray.set_startup(v_startup.get())
                except OSError as e:
                    messagebox.showerror("Settings", f"Couldn't change Start with Windows:\n{e}", parent=w)
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
        try:
            update = updater.check()
        except (OSError, ValueError):
            return
        if update:
            print(f"[update] {update['tag']} is available (running {VERSION})")
            self.ui.put(lambda: self._show_update(update))

    def _show_update(self, update):
        self.update = update
        if updater.can_self_update() and update["exe_url"]:
            self.update_btn = self.btn(self.update_lbl.master, f"↓  Update to {update['tag']}", self.start_update,
                                       kind="accent")
            self.update_btn.pack(side="right", padx=(0, self.px(10)))
        else:  # running from source: point to the release page
            self.update_lbl.config(text=f"Update available: {update['tag']} ↗")
            self.update_lbl.bind("<Button-1>", lambda e: webbrowser.open(update["page"]))
            self.update_lbl.pack(side="right", padx=(0, self.px(4)))

    def start_update(self):
        if self.updating:
            return
        if not messagebox.askyesno(
                "Update Vinyl Presence",
                f"Download and install {self.update['tag']} now?\n\nVinyl Presence restarts by itself. "
                "If a record is playing, it keeps playing.", parent=self.root):
            return
        self.updating = True
        self.update_btn.config(text="Downloading… 0%")

        def work():
            try:
                new = updater.download(self.update, lambda pct: self.ui.put(
                    lambda: self.update_btn.config(text=f"Downloading… {pct}%")))
            except Exception as e:
                msg = str(e)
                self.ui.put(lambda: self._update_failed(msg))
                return
            self.ui.put(lambda: self._install_update(new))
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _install_update(self, new):
        self.update_btn.config(text="Restarting…")
        self.root.update_idletasks()
        try:
            self.core.player.save_state()
            updater.restart_into(new)
        except OSError as e:
            return self._update_failed(str(e))
        print(f"[update] installing {self.update['tag']} and restarting")
        self.close()

    def _update_failed(self, msg):
        self.updating = False
        self.update_btn.config(text=f"↓  Update to {self.update['tag']}")
        if messagebox.askyesno("Update failed", f"The update couldn't be installed:\n{msg}\n\n"
                               "Open the download page instead?", parent=self.root):
            webbrowser.open(self.update["page"])

    # ------------------------------------------------------------ tray & windows

    def _start_tray(self):
        if not tray.available():
            return
        try:
            self.tray = tray.Tray(
                ASSETS / "vinyl.png", self._tray_now_text, lambda: bool(self.core.player.now),
                on_open=lambda: self.ui.put(self.show_window),
                on_stop=lambda: self.ui.put(lambda: self.bg(self.core.player.control, "stop")),
                on_quit=lambda: self.ui.put(self.close))
            tray.refresh_startup()
        except Exception as e:  # no tray is fine; the window works as before
            print(f"[tray] {e}")
            self.tray = None

    def _tray_now_text(self):
        snap = self.core.player.snapshot()
        if not snap:
            return "Nothing playing"
        rec = snap["record"]
        title = snap["tracks"][snap["idx"]]["title"] if snap["idx"] is not None else rec["title"]
        text = f"▶ {title} — {rec['artist']}"
        return text if len(text) <= 60 else text[:59] + "…"

    def show_window(self):
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def on_window_close(self):
        """The X button: keep running in the tray (if there is one), otherwise quit."""
        if not (self.tray and self.core.config.get("close_to_tray", True)):
            return self.close()
        self.root.withdraw()
        if not self.core.config.get("tray_hint_shown"):
            self.tray.notify("Vinyl Presence keeps running here, so your status stays on. "
                             "Right-click the record icon to quit.", "Still spinning")
            self.core.update_config({"tray_hint_shown": True})

    def open_stats(self):
        if self.stats_win and self.stats_win.win.winfo_exists():
            self.stats_win.render()
            self.stats_win.win.lift()
            return
        self.core.player.flush()  # include the record that's playing right now
        self.stats_win = StatsWindow(self)

    def close(self):
        """Really quit: clear the Discord status and remove the tray icon."""
        self.closed = True
        self.root.withdraw()
        if self.tray:
            self.tray.stop()
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
    updater.cleanup()
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
    if not lock:
        # Already running (maybe hidden in the tray): ask that copy to show its window.
        SHOW_REQUEST.touch()
        time.sleep(2)
        if SHOW_REQUEST.exists():  # an older version that doesn't listen for this
            SHOW_REQUEST.unlink(missing_ok=True)
            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo("Vinyl Presence", "Vinyl Presence is already open. Check your taskbar.")
        return
    SHOW_REQUEST.unlink(missing_ok=True)
    root = tk.Tk()
    root.title("Vinyl Presence")
    root.configure(bg=BG)
    scale = root.winfo_fpixels("1i") / 96
    root.geometry(f"{int(1120 * scale)}x{int(760 * scale)}")
    root.minsize(int(860 * scale), int(600 * scale))
    root.iconphoto(True, vinyl_image(64))
    dark_titlebar(root)
    core = Core()
    app = VinylApp(root, core)
    root.protocol("WM_DELETE_WINDOW", app.on_window_close)
    if "--tray" in sys.argv and app.tray:  # started with Windows: stay in the tray
        root.withdraw()
    root.mainloop()


if __name__ == "__main__":
    main()
