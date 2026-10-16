"""The stats window: hours listened, most played records and artists, hours per month."""
import os
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import achievements
import stats
import value as collection_value
from covers import ThumbCache
from theme import ACCENT, BAD, BG, CHART, FAINT, FONT, GOOD, LINE, MUTED, PANEL, PANEL2, TEXT, WARN

try:
    import wrapped  # the shareable picture needs Pillow
except ImportError:
    wrapped = None


def fmt_hours(secs):
    h = secs / 3600
    if h >= 10:
        return f"{h:.0f} h"
    if h >= 1:
        return f"{h:.1f} h"
    return f"{round(secs / 60)} min"


class StatsWindow:
    def __init__(self, app):
        self.app = app
        px = app.px
        w = self.win = tk.Toplevel(app.root, bg=BG)
        w.title("Your listening")
        w.transient(app.root)
        w.minsize(px(760), px(560))
        w.bind("<Escape>", lambda e: w.destroy())
        self.thumbs = ThumbCache(app, px(36))

        pad = tk.Frame(w, bg=BG)
        pad.pack(fill="both", expand=True, padx=px(22), pady=px(18))
        head = tk.Frame(pad, bg=BG)
        head.pack(fill="x")
        tk.Label(head, text="Your listening", bg=BG, fg=TEXT, font=(FONT, 16, "bold")).pack(side="left")
        self.period = ttk.Combobox(head, values=stats.PERIODS, state="readonly", width=14, font=(FONT, 10))
        self.period.set(stats.PERIODS[0])
        self.period.pack(side="right")
        self.period.bind("<<ComboboxSelected>>", lambda e: self.render())
        app.btn(head, "Achievements", self.show_achievements, size=9).pack(side="right", padx=(0, px(8)))
        if wrapped:
            app.btn(head, "Save image…", self.save_image, size=9).pack(side="right", padx=(0, px(8)))
            app.btn(head, "Copy image", self.copy_image, kind="accent", size=9).pack(side="right", padx=(0, px(6)))
            self.share_lbl = tk.Label(head, bg=BG, fg=FAINT, font=(FONT, 9))
            self.share_lbl.pack(side="right", padx=(0, px(10)))

        self.tiles = tk.Frame(pad, bg=BG)
        self.tiles.pack(fill="x", pady=(px(14), 0))
        for i in range(4):
            self.tiles.columnconfigure(i, weight=1, uniform="tile")

        stylus = tk.Frame(pad, bg=PANEL)
        stylus.pack(fill="x", pady=(px(14), 0))
        self.stylus_lbl = tk.Label(stylus, bg=PANEL, fg=TEXT, font=(FONT, 10), anchor="w")
        self.stylus_lbl.pack(side="left", padx=(px(16), px(12)), pady=px(10))
        self.stylus_bar = tk.Canvas(stylus, height=px(8), width=px(200), bg=PANEL, highlightthickness=0)
        self.stylus_bar.pack(side="left", fill="x", expand=True, padx=(0, px(16)))
        self.stylus_bar.bind("<Configure>", lambda e: self.draw_stylus())

        # what the collection is worth (only when switched on in Settings → Records)
        self.value_box = tk.Frame(pad, bg=PANEL)
        if app.core.config.get("value_enabled"):
            self.value_box.pack(fill="x", pady=(px(14), 0))
            self.value_lbl = tk.Label(self.value_box, bg=PANEL, fg=TEXT, font=(FONT, 10), anchor="w", justify="left")
            self.value_lbl.pack(side="left", padx=(px(16), px(12)), pady=px(10))
            app.btn(self.value_box, "Most valuable…", self.show_valuable, size=9).pack(side="right", padx=px(12))
            self.render_value()

        chart_box = tk.Frame(pad, bg=PANEL)
        chart_box.pack(fill="x", pady=(px(14), 0))
        tk.Label(chart_box, text="Hours per month", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"),
                 anchor="w").pack(fill="x", padx=px(16), pady=(px(12), 0))
        chart_h = px(120) if app.core.config.get("value_enabled") else px(170)  # room for the value strip
        self.chart = tk.Canvas(chart_box, height=chart_h, bg=PANEL, highlightthickness=0)
        self.chart.pack(fill="x", padx=px(16), pady=(px(4), px(12)))
        self.chart.bind("<Configure>", lambda e: self.draw_chart())

        lists = tk.Frame(pad, bg=BG)
        lists.pack(fill="both", expand=True, pady=(px(14), 0))
        lists.columnconfigure(0, weight=3, uniform="list")
        lists.columnconfigure(1, weight=2, uniform="list")
        self.top_records = self._panel(lists, "Most played records", 0)
        self.top_artists = self._panel(lists, "Top artists", 1)

        self.render()
        w.update_idletasks()
        h = min(w.winfo_reqheight(), w.winfo_screenheight() - px(130))  # never taller than the screen
        x = app.root.winfo_rootx() + (app.root.winfo_width() - w.winfo_reqwidth()) // 2
        y = min(app.root.winfo_rooty() + px(30), w.winfo_screenheight() - h - px(100))
        w.geometry(f"{w.winfo_reqwidth()}x{h}+{max(0, x)}+{max(0, y)}")

    def _panel(self, parent, title, col):
        px = self.app.px
        box = tk.Frame(parent, bg=PANEL)
        box.grid(row=0, column=col, sticky="nsew", padx=(0, px(14)) if col == 0 else 0)
        tk.Label(box, text=title, bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"), anchor="w").pack(
            fill="x", padx=px(16), pady=(px(12), px(6)))
        body = tk.Frame(box, bg=PANEL)
        body.pack(fill="both", expand=True, padx=px(16), pady=(0, px(12)))
        return body

    # -- content

    def render(self):
        data = stats.compute(self.app.core, self.period.get())
        top_artist = data["top_artists"][0][0] if data["top_artists"] else "–"
        for child in self.tiles.winfo_children():
            child.destroy()
        tiles = [(fmt_hours(data["seconds"]) if data["seconds"] else "0 h", "listened"),
                 (str(data["sessions"]), "records played"),
                 (str(data["records"]), "different records"),
                 (top_artist, "top artist")]
        for i, (value, label) in enumerate(tiles):
            self._tile(i, value, label)
        self._fill_records(data["top_records"][:8] if self.app.core.config.get("value_enabled") else data["top_records"])
        self._fill_artists(data["top_artists"])
        self.months = stats.months(self.app.core)
        self.draw_chart()
        self.draw_stylus()

    def _tile(self, col, value, label):
        px = self.app.px
        t = tk.Frame(self.tiles, bg=PANEL)
        t.grid(row=0, column=col, sticky="nsew", padx=(0, px(10)) if col < 3 else 0)
        big = (FONT, 20, "bold") if len(value) <= 10 else (FONT, 13, "bold")
        tk.Label(t, text=value, bg=PANEL, fg=TEXT, font=big, anchor="w", wraplength=px(170),
                 justify="left").pack(fill="x", padx=px(14), pady=(px(10), 0))
        tk.Label(t, text=label, bg=PANEL, fg=FAINT, font=(FONT, 9), anchor="w").pack(
            fill="x", padx=px(14), pady=(0, px(10)))

    def _empty(self, body, text):
        tk.Label(body, text=text, bg=PANEL, fg=FAINT, font=(FONT, 10), anchor="w").pack(fill="x", pady=4)

    def _fill_records(self, rows):
        body = self.top_records
        for child in body.winfo_children():
            child.destroy()
        if not rows:
            return self._empty(body, "Nothing played in this period yet.")
        for rank, (rec, plays, secs) in enumerate(rows, 1):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=2)
            tk.Label(row, text=str(rank), bg=PANEL, fg=FAINT, font=(FONT, 9), width=3, anchor="w").pack(side="left")
            art = tk.Label(row, bg=PANEL, image=self.thumbs.get(rec["id"], self._thumb_ready)
                           or self.thumbs.placeholder or "")
            art.rid = rec["id"]
            art.pack(side="left", padx=(0, self.app.px(10)))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=rec["title"], bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
            tk.Label(text, text=rec["artist"], bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
            tk.Label(row, text=f"{plays}× · {fmt_hours(secs)}", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="right")

    def _thumb_ready(self, rid):
        if not self.win.winfo_exists():
            return
        for row in self.top_records.winfo_children():
            for w in row.winfo_children():
                if getattr(w, "rid", None) == rid:
                    w.config(image=self.thumbs.images[rid])

    def _fill_artists(self, rows):
        body = self.top_artists
        for child in body.winfo_children():
            child.destroy()
        if not rows:
            return self._empty(body, "–")
        for rank, (name, plays, secs) in enumerate(rows, 1):
            row = tk.Frame(body, bg=PANEL)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=str(rank), bg=PANEL, fg=FAINT, font=(FONT, 9), width=3, anchor="w").pack(side="left")
            tk.Label(row, text=name, bg=PANEL, fg=TEXT, font=(FONT, 10), anchor="w").pack(side="left", fill="x",
                                                                                          expand=True)
            tk.Label(row, text=f"{plays}× · {fmt_hours(secs)}", bg=PANEL, fg=MUTED, font=(FONT, 9)).pack(side="right")

    # -- achievements

    def _emoji(self, char, size, dim=False):
        """A color emoji as a Tk image (None without Pillow or the Windows emoji font)."""
        key = (char, size, dim)
        cache = self.__dict__.setdefault("_emojis", {})
        if key not in cache:
            try:
                from PIL import Image, ImageDraw, ImageFont, ImageTk
                font = ImageFont.truetype(os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "seguiemj.ttf"),
                                          size * 2)
                img = Image.new("RGBA", (size * 3, size * 3), (0, 0, 0, 0))
                ImageDraw.Draw(img).text((size // 2, size // 4), char, font=font, embedded_color=True)
                img = img.crop(img.getbbox() or (0, 0, 1, 1))
                img.thumbnail((size, size), Image.LANCZOS)
                if dim:  # not earned yet: grey and faded
                    alpha = img.getchannel("A").point(lambda a: a * 45 // 100)
                    img = img.convert("LA").convert("RGBA")
                    img.putalpha(alpha)
                cache[key] = ImageTk.PhotoImage(img)
            except (ImportError, OSError, ValueError):
                cache[key] = None
        return cache[key]

    def show_achievements(self):
        px = self.app.px
        items = achievements.progress(self.app.core)
        earned = sorted((a for a in items if a["at"]), key=lambda a: -a["at"])
        todo = sorted((a for a in items if not a["at"]),
                      key=lambda a: -(a["have"] / a["goal"]) if a["goal"] else 1)
        w = tk.Toplevel(self.win, bg=BG)
        w.title("Achievements")
        w.transient(self.win)
        w.bind("<Escape>", lambda e: w.destroy())
        head = tk.Frame(w, bg=BG)
        head.pack(fill="x", padx=px(20), pady=(px(16), px(10)))
        tk.Label(head, text="Achievements", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(side="left")
        tk.Label(head, text=f"{len(earned)} of {len(items)} earned", bg=BG, fg=MUTED, font=(FONT, 10)).pack(
            side="left", padx=(px(10), 0), pady=(px(4), 0))

        box = tk.Frame(w, bg=BG)
        box.pack(fill="both", expand=True, padx=(px(20), px(8)), pady=(0, px(16)))
        canvas = tk.Canvas(box, bg=BG, highlightthickness=0)
        bar = ttk.Scrollbar(box, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        grid = tk.Frame(canvas, bg=BG)
        canvas.create_window((0, 0), window=grid, anchor="nw")
        grid.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        w.bind("<MouseWheel>", lambda e: canvas.yview_scroll(-e.delta // 120, "units"))
        for col in (0, 1):
            grid.columnconfigure(col, weight=1, uniform="badge")

        for i, a in enumerate(earned + todo):
            card = tk.Frame(grid, bg=PANEL)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0, px(10)), pady=(0, px(10)))
            icon = self._emoji(a["icon"], px(30), dim=not a["at"])
            tk.Label(card, image=icon, text="" if icon else a["icon"], bg=PANEL, fg=TEXT, font=(FONT, 18),
                     width=px(40) if icon else 3).pack(side="left", padx=(px(12), px(10)), pady=px(12))
            text = tk.Frame(card, bg=PANEL)
            text.pack(side="left", fill="x", expand=True, pady=px(10), padx=(0, px(12)))
            tk.Label(text, text=a["name"], bg=PANEL, fg=TEXT if a["at"] else MUTED, font=(FONT, 10, "bold"),
                     anchor="w").pack(fill="x")
            tk.Label(text, text=a["desc"], bg=PANEL, fg=MUTED if a["at"] else FAINT, font=(FONT, 9), anchor="w",
                     justify="left", wraplength=px(230)).pack(fill="x")
            if a["at"]:
                when = time.strftime("%d %B %Y", time.localtime(a["at"])).lstrip("0")
                tk.Label(text, text=f"Earned {when}", bg=PANEL, fg=ACCENT, font=(FONT, 8), anchor="w").pack(fill="x")
            elif a["goal"]:
                row = tk.Frame(text, bg=PANEL)
                row.pack(fill="x", pady=(px(3), 0))
                meter = tk.Canvas(row, width=px(110), height=px(6), bg=PANEL, highlightthickness=0)
                meter.pack(side="left")
                r = px(3)
                meter.create_line(r, r, px(110) - r, r, width=px(6), capstyle="round", fill=LINE)
                frac = min(1.0, a["have"] / a["goal"])
                if frac > 0:
                    meter.create_line(r, r, r + (px(110) - 2 * r) * frac, r, width=px(6), capstyle="round", fill=ACCENT)
                tk.Label(row, text=f"{a['have']:,.0f} / {a['goal']:,}", bg=PANEL, fg=FAINT, font=(FONT, 8)).pack(
                    side="left", padx=(px(8), 0))
        w.update_idletasks()
        width = grid.winfo_reqwidth() + px(50)
        canvas.configure(width=grid.winfo_reqwidth())
        w.geometry(f"{width}x{min(w.winfo_screenheight() - px(160), grid.winfo_reqheight() + px(80))}")

    # -- collection value

    def render_value(self):
        if not self.win.winfo_exists():
            return
        s = self.app.core.value.summary()
        cur = s["currency"]
        if s["priced"]:
            text = f"Your collection is worth about {collection_value.money(s['total'], cur)}"
            detail = f"{s['priced']} of {s['records']} records are for sale on Discogs; this adds up their lowest prices."
        else:
            text = "Your collection's value"
            detail = "Looking up prices on Discogs. The first time takes a while."
        if s["busy"] and s["priced"]:
            detail += f" Still looking up: {s['looked_up']} of {s['records']} done."
        if s["estimate"]:
            e = s["estimate"]
            detail += f"\nDiscogs' own estimate: {e.get('minimum')} to {e.get('maximum')} (median {e.get('median')})."
        self.value_lbl.config(text=f"{text}\n{detail}")
        self.win.after(5000 if s["busy"] or not s["looked_up"] else 60000, self.render_value)

    def show_valuable(self):
        px, core = self.app.px, self.app.core
        s = core.value.summary()
        w = tk.Toplevel(self.win, bg=BG)
        w.title("Most valuable records")
        w.transient(self.win)
        w.bind("<Escape>", lambda e: w.destroy())
        pad = tk.Frame(w, bg=BG)
        pad.pack(fill="both", expand=True, padx=px(20), pady=px(16))
        tk.Label(pad, text="Most valuable records", bg=BG, fg=TEXT, font=(FONT, 14, "bold"), anchor="w").pack(fill="x")
        tk.Label(pad, text="The lowest price each record is for sale at on Discogs right now. Prices are refreshed "
                           "every week. Click a record to see it for sale.", bg=BG, fg=MUTED, font=(FONT, 9),
                 anchor="w", justify="left", wraplength=px(460)).pack(fill="x", pady=(px(2), px(10)))
        body = tk.Frame(pad, bg=PANEL)
        body.pack(fill="both", expand=True)
        if not s["top"]:
            tk.Label(body, text="No prices yet. Give it a few minutes.", bg=PANEL, fg=FAINT, font=(FONT, 10)).pack(
                padx=px(16), pady=px(16))
        arts = {}

        def cover_ready(rid):
            if rid in arts and arts[rid].winfo_exists():
                arts[rid].config(image=self.thumbs.images[rid])

        for rank, (rec, price, for_sale) in enumerate(s["top"][:10], 1):
            row = tk.Frame(body, bg=PANEL, cursor="hand2")
            row.pack(fill="x", padx=px(12), pady=px(3))
            tk.Label(row, text=str(rank), bg=PANEL, fg=FAINT, font=(FONT, 9), width=3, anchor="w").pack(side="left")
            art = arts[rec["id"]] = tk.Label(row, bg=PANEL, image=self.thumbs.get(rec["id"], cover_ready)
                                             or self.thumbs.placeholder or "")
            art.pack(side="left", padx=(0, px(10)))
            # the price first, so a long title is cut off instead of the price
            tk.Label(row, text=f"{collection_value.money(price, s['currency'])}\n{for_sale} for sale", bg=PANEL,
                     fg=ACCENT, font=(FONT, 10, "bold"), justify="right").pack(side="right", padx=(px(10), 0))
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            tk.Label(text, text=rec["title"], bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"), anchor="w").pack(fill="x")
            tk.Label(text, text=rec["artist"], bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
            url = f"https://www.discogs.com/sell/release/{rec['id']}"
            for widget in (row, art, text, *text.winfo_children()):
                widget.bind("<Button-1>", lambda e, url=url: webbrowser.open(url))
        w.update_idletasks()
        w.geometry(f"{px(560)}x{min(w.winfo_screenheight() - px(120), w.winfo_reqheight())}")

    # -- stylus wear

    def draw_stylus(self):
        cfg = self.app.core.config
        life = max(1, cfg.get("stylus_life_hours") or 1000)
        hours = stats.stylus_hours(self.app.core)
        frac = min(1.0, hours / life)
        self.stylus_lbl.config(text=f"Stylus: {hours:,.0f} of about {life:,} hours")
        c, px = self.stylus_bar, self.app.px
        c.delete("all")
        w, h = c.winfo_width(), px(8)
        if w < 20:
            return
        r = h / 2
        c.create_line(r, r, w - r, r, width=h, capstyle="round", fill=LINE)
        color = GOOD if frac < 0.75 else WARN if frac < 1 else BAD  # status colours: fine / soon / replace
        if frac > 0:
            c.create_line(r, r, max(r, r + (w - h) * frac), r, width=h, capstyle="round", fill=color)

    # -- the shareable picture

    def _make_image(self, then):
        period = self.period.get()
        self.share_lbl.config(text="Making the picture…", fg=FAINT)

        def work():
            try:
                img = wrapped.make(self.app.core, period)
            except Exception as e:
                msg = str(e)
                self.app.ui.put(lambda: self.share_lbl.config(text=f"Couldn't make it: {msg}", fg=BAD))
                return
            self.app.ui.put(lambda: self.win.winfo_exists() and then(img))
        threading.Thread(target=work, daemon=True, name="wrapped").start()

    def copy_image(self):
        def done(img):
            try:
                wrapped.copy_to_clipboard(img)
            except OSError as e:
                self.share_lbl.config(text=str(e), fg=BAD)
                return
            self.share_lbl.config(text="Copied! Paste it in Discord with Ctrl+V", fg=ACCENT)
        self._make_image(done)

    def save_image(self):
        def done(img):
            path = filedialog.asksaveasfilename(parent=self.win, title="Save your Vinyl Wrapped",
                                                defaultextension=".png", initialfile="Vinyl Wrapped.png",
                                                filetypes=[("PNG image", "*.png")])
            if path:
                img.save(path)
                self.share_lbl.config(text="Saved", fg=ACCENT)
            else:
                self.share_lbl.config(text="")
        self._make_image(done)

    # -- chart: one series, so no legend; the title names it

    def draw_chart(self):
        c, px = self.chart, self.app.px
        c.delete("all")
        months = getattr(self, "months", None)
        width, height = c.winfo_width(), c.winfo_height()
        if not months or width < 50:
            return
        axis_w, label_h = px(34), px(20)
        plot_w, plot_h = width - axis_w, height - label_h - px(8)
        top = px(8)
        peak = max(h for _, h in months)
        step = next(s for s in (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500) if peak / s <= 4) if peak else 1
        y_max = step * max(1, -(-peak // step)) if peak else 4 * step

        def y(v):
            return top + plot_h - v / y_max * plot_h

        # recessive grid + clean ticks
        v = 0.0
        while v <= y_max + 1e-9:
            c.create_line(axis_w, y(v), width, y(v), fill=LINE if v else PANEL2, width=1)
            c.create_text(axis_w - px(8), y(v), text=f"{v:g}", anchor="e", fill=FAINT, font=(FONT, 8))
            v += step

        slot = plot_w / len(months)
        bar_w = min(px(24), slot * 0.6)
        r = min(px(4), bar_w / 2)
        for i, (label, hours) in enumerate(months):
            cx = axis_w + slot * (i + 0.5)
            x0, x1 = cx - bar_w / 2, cx + bar_w / 2
            # a hit target taller and wider than the bar, so hovering is easy
            tag = f"m{i}"
            hit = c.create_rectangle(cx - slot / 2, top, cx + slot / 2, top + plot_h, fill=PANEL, outline="",
                                     tags=tag)
            c.tag_lower(hit)
            if hours > 0:
                yt, yb = y(hours), y(0)
                if yb - yt > r:  # rounded data-end, square at the baseline
                    c.create_rectangle(x0, yt + r, x1, yb, fill=CHART, outline="", tags=tag)
                    c.create_rectangle(x0 + r, yt, x1 - r, yt + r, fill=CHART, outline="", tags=tag)
                    c.create_oval(x0, yt, x0 + 2 * r, yt + 2 * r, fill=CHART, outline="", tags=tag)
                    c.create_oval(x1 - 2 * r, yt, x1, yt + 2 * r, fill=CHART, outline="", tags=tag)
                else:
                    c.create_rectangle(x0, max(yt, yb - 2), x1, yb, fill=CHART, outline="", tags=tag)
            c.create_text(cx, top + plot_h + px(6), text=label, anchor="n", fill=FAINT, font=(FONT, 8))
            c.tag_bind(tag, "<Enter>", lambda e, cx=cx, label=label, hours=hours: self._tip(cx, label, hours))
            c.tag_bind(tag, "<Leave>", lambda e: c.delete("tip"))

    def _tip(self, cx, label, hours):
        c, px = self.chart, self.app.px
        c.delete("tip")
        text = f"{label}: {fmt_hours(hours * 3600)}" if hours else f"{label}: nothing played"
        t = c.create_text(cx, px(14), text=text, fill=TEXT, font=(FONT, 9, "bold"), tags="tip", state="disabled")
        x0, y0, x1, y1 = c.bbox(t)
        shift = max(0, px(2) - x0) - max(0, x1 - c.winfo_width() + px(2))
        c.move(t, shift, 0)
        bg = c.create_rectangle(x0 + shift - px(8), y0 - px(4), x1 + shift + px(8), y1 + px(4),
                                fill=PANEL2, outline=LINE, tags="tip", state="disabled")
        c.tag_raise(t, bg)
