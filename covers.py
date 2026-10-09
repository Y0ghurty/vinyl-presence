"""Cover wall: browse the collection as album covers instead of a text list.

Tiles are drawn on a Canvas only once they scroll into view, and covers load in
the background (newest request first, so what's on screen comes first). Small
copies are kept in data/thumbs, so after the first time the wall fills instantly.
"""
import hashlib
import io
import queue
import threading
import tkinter as tk
import tkinter.font as tkfont
import urllib.request
from collections import OrderedDict
from tkinter import ttk

from core import ASSETS, DATA
from metadata import USER_AGENT
from theme import ACCENT, FONT, MUTED, PANEL, PANEL2, TEXT

try:
    from PIL import Image, ImageDraw, ImageOps, ImageTk
except ImportError:
    Image = None


class ThumbCache:
    """Square cover thumbnails of one size, loaded off the UI thread."""

    MAX_IMAGES = 600

    def __init__(self, app, size):
        self.app, self.size = app, size
        self.images = OrderedDict()  # record id -> PhotoImage (least recently used first)
        self.waiting = {}  # record id -> callbacks to run when its image is ready
        self.failed = set()  # don't keep retrying covers that didn't load
        self.jobs = queue.LifoQueue()
        self.placeholder = None
        if Image:
            self.placeholder = ImageTk.PhotoImage(self._round(
                Image.open(ASSETS / "vinyl.png").convert("RGB").resize((size, size), Image.LANCZOS)))
            for _ in range(4):
                threading.Thread(target=self._worker, daemon=True, name="thumbs").start()

    def get(self, rid, callback=None):
        """The image if it's loaded; otherwise start loading it and call callback(rid) when it is."""
        if rid in self.images:
            self.images.move_to_end(rid)
            return self.images[rid]
        if not Image or rid in self.failed:
            return None
        info = self.app.core.library.get_cached(rid) or {}
        url = (info.get("thumb") if self.size <= 150 else None) or info.get("cover") or info.get("thumb")
        if not url:
            return None
        first = rid not in self.waiting
        self.waiting.setdefault(rid, [])
        if callback:
            self.waiting[rid].append(callback)
        if first:
            self.jobs.put((rid, url))
        return None

    def _round(self, img):
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=6, fill=255)
        framed = Image.new("RGB", img.size, PANEL)
        framed.paste(img, (0, 0), mask)
        return framed

    def _worker(self):
        while True:
            rid, url = self.jobs.get()
            path = DATA / "thumbs" / f"{hashlib.md5(f'{url}@{self.size}'.encode()).hexdigest()[:16]}.jpg"
            img = None
            try:
                if path.exists():
                    img = Image.open(path).convert("RGB")
                else:
                    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = resp.read()
                    img = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGB"), (self.size, self.size),
                                       Image.LANCZOS)
                    path.parent.mkdir(exist_ok=True)
                    img.save(path, quality=88)
                img = self._round(img)
            except Exception as e:
                print(f"[thumbs] {url}: {e}")
            self.app.ui.put(lambda rid=rid, img=img: self._ready(rid, img))

    def _ready(self, rid, img):
        callbacks = self.waiting.pop(rid, [])
        if img is None:
            self.failed.add(rid)
            return
        self.images[rid] = ImageTk.PhotoImage(img)
        while len(self.images) > self.MAX_IMAGES:
            self.images.popitem(last=False)
        for cb in callbacks:
            cb(rid)


class CoverGrid(tk.Frame):
    GAP = 14

    def __init__(self, parent, app, on_play):
        super().__init__(parent, bg=PANEL)
        self.app, self.on_play = app, on_play
        self.tile = app.px(128)
        self.text_h = app.px(40)
        self.thumbs = ThumbCache(app, self.tile)
        self.title_font = tkfont.Font(family=FONT, size=9, weight="bold")
        self.artist_font = tkfont.Font(family=FONT, size=9)
        self.items, self.cols, self.sel, self.playing = [], 1, -1, None
        self.gap_x = self.GAP  # horizontal gap, widened so the columns fill the width evenly
        self.drawn = {}  # index -> canvas item ids of its tile

        self.canvas = tk.Canvas(self, bg=PANEL, highlightthickness=0, yscrollincrement=app.px(24))
        sb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=lambda a, b: (sb.set(a, b), self._draw_visible()))
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda e: self._layout())
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Double-1>", self._double)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    # -- data

    def set_items(self, items, select_first=False):
        self.items = items
        self.sel = 0 if select_first and items else -1
        self.canvas.yview_moveto(0)
        self._layout()

    def selected_id(self):
        return self.items[self.sel]["id"] if 0 <= self.sel < len(self.items) else None

    def select_id(self, rid):
        for i, r in enumerate(self.items):
            if r["id"] == rid:
                self._select(i)
                return

    def move(self, delta):
        """Arrow keys from the search box: ±1 row (delta in rows)."""
        if not self.items:
            return
        i = (self.sel if self.sel >= 0 else -self.cols) + delta * self.cols
        self._select(max(0, min(len(self.items) - 1, i)))

    def set_playing(self, rid):
        old, self.playing = self.playing, rid
        for i, r in enumerate(self.items):
            if r["id"] in (old, rid) and i in self.drawn:
                self._redraw(i)

    def refresh_missing(self):
        """Covers that had no URL yet (still being looked up) may have one now."""
        for i in list(self.drawn):
            rid = self.items[i]["id"]
            if rid not in self.thumbs.images:
                self.thumbs.get(rid, self._image_ready)

    # -- layout & drawing

    def _cell(self, i):
        col, row = i % self.cols, i // self.cols
        return self.gap_x + col * (self.tile + self.gap_x), self.GAP + row * (self.tile + self.text_h + self.GAP)

    def _layout(self):
        width = max(1, self.canvas.winfo_width())
        self.cols = max(1, (width - self.GAP) // (self.tile + self.GAP))
        self.gap_x = max(self.GAP, (width - self.cols * self.tile) / (self.cols + 1))
        rows = -(-len(self.items) // self.cols)
        height = self.GAP + rows * (self.tile + self.text_h + self.GAP)
        self.canvas.delete("all")
        self.drawn = {}
        self.canvas.configure(scrollregion=(0, 0, width, height))
        self._draw_visible()
        if self.sel >= 0:
            self._draw_selection()

    def _draw_visible(self):
        if not self.items:
            return
        top = self.canvas.canvasy(0)
        bottom = top + self.canvas.winfo_height()
        row_h = self.tile + self.text_h + self.GAP
        first = max(0, int(top // row_h) - 1) * self.cols
        last = min(len(self.items), (int(bottom // row_h) + 2) * self.cols)
        for i in range(first, last):
            if i not in self.drawn:
                self._draw_tile(i)

    def _fit(self, text, font):
        if font.measure(text) <= self.tile:
            return text
        while text and font.measure(text + "…") > self.tile:
            text = text[:-1]
        return text.rstrip() + "…"

    def _draw_tile(self, i):
        r = self.items[i]
        x, y = self._cell(i)
        c = self.canvas
        img = self.thumbs.get(r["id"], self._image_ready) or self.thumbs.placeholder
        ids = []
        if img:
            ids.append(c.create_image(x, y, image=img, anchor="nw"))
        else:
            ids.append(c.create_rectangle(x, y, x + self.tile, y + self.tile, fill=PANEL2, outline=""))
        ids.append(c.create_text(x, y + self.tile + self.app.px(6), anchor="nw", fill=TEXT, font=self.title_font,
                                 text=self._fit(r["title"], self.title_font)))
        ids.append(c.create_text(x, y + self.tile + self.app.px(22), anchor="nw", fill=MUTED, font=self.artist_font,
                                 text=self._fit(r["artist"], self.artist_font)))
        if r["id"] == self.playing:
            d = self.app.px(22)
            ids.append(c.create_oval(x + self.tile - d - 6, y + 6, x + self.tile - 6, y + 6 + d, fill=ACCENT, outline=""))
            ids.append(c.create_text(x + self.tile - 6 - d / 2, y + 6 + d / 2, text="▶", fill="#1d120b",
                                     font=(FONT, 8, "bold")))
        self.drawn[i] = ids

    def _redraw(self, i):
        for item in self.drawn.pop(i, []):
            self.canvas.delete(item)
        self._draw_tile(i)
        if i == self.sel:
            self._draw_selection()

    def _image_ready(self, rid):
        for i in list(self.drawn):
            if self.items[i]["id"] == rid:
                self._redraw(i)

    def _draw_selection(self):
        self.canvas.delete("sel")
        if 0 <= self.sel < len(self.items):
            x, y = self._cell(self.sel)
            self.canvas.create_rectangle(x - 3, y - 3, x + self.tile + 3, y + self.tile + self.text_h,
                                         outline=ACCENT, width=2, tags="sel")

    def _select(self, i):
        self.sel = i
        self._draw_selection()
        x, y = self._cell(i)
        total = float(self.canvas.cget("scrollregion").split()[3] or 1)
        top, bottom = self.canvas.canvasy(0), self.canvas.canvasy(self.canvas.winfo_height())
        if y - self.GAP < top:
            self.canvas.yview_moveto(max(0, (y - self.GAP) / total))
        elif y + self.tile + self.text_h > bottom:
            self.canvas.yview_moveto(max(0, (y + self.tile + self.text_h + self.GAP - (bottom - top)) / total))

    # -- mouse

    def _index_at(self, e):
        x, y = self.canvas.canvasx(e.x), self.canvas.canvasy(e.y)
        col = int(max(0, x - self.gap_x / 2) // (self.tile + self.gap_x))
        row = int((y - self.GAP) // (self.tile + self.text_h + self.GAP))
        i = row * self.cols + col
        return i if 0 <= col < self.cols and 0 <= i < len(self.items) else -1

    def _click(self, e):
        i = self._index_at(e)
        if i >= 0:
            self._select(i)

    def _double(self, e):
        i = self._index_at(e)
        if i >= 0:
            self._select(i)
            self.on_play(self.items[i]["id"])

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120) * 3, "units")
