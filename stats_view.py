"""The stats window: hours listened, most played records and artists, hours per month."""
import tkinter as tk
from tkinter import ttk

import stats
from covers import ThumbCache
from theme import BG, CHART, FAINT, FONT, LINE, MUTED, PANEL, PANEL2, TEXT


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

        self.tiles = tk.Frame(pad, bg=BG)
        self.tiles.pack(fill="x", pady=(px(14), 0))
        for i in range(4):
            self.tiles.columnconfigure(i, weight=1, uniform="tile")

        chart_box = tk.Frame(pad, bg=PANEL)
        chart_box.pack(fill="x", pady=(px(14), 0))
        tk.Label(chart_box, text="Hours per month", bg=PANEL, fg=MUTED, font=(FONT, 9, "bold"),
                 anchor="w").pack(fill="x", padx=px(16), pady=(px(12), 0))
        self.chart = tk.Canvas(chart_box, height=px(170), bg=PANEL, highlightthickness=0)
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
        x = app.root.winfo_rootx() + (app.root.winfo_width() - w.winfo_width()) // 2
        w.geometry(f"+{max(0, x)}+{max(0, app.root.winfo_rooty() + px(30))}")

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
        self._fill_records(data["top_records"])
        self._fill_artists(data["top_artists"])
        self.months = stats.months(self.app.core)
        self.draw_chart()

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
