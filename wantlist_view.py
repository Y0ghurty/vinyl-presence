"""The "Want list" window: who in Vinyl Hangout has a record you're looking for, and who wants one you have.

Needs "Want-list matches" switched on (Settings → Sharing & tray), which shares your collection and Discogs
want list with the members who switched it on too.
"""
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from covers import ThumbCache
from theme import ACCENT, BG, FAINT, FONT, MUTED, PANEL, TEXT


class WantListWindow:
    def __init__(self, app):
        self.app = app
        px = app.px
        w = self.win = tk.Toplevel(app.root, bg=BG)
        w.title("Want list")
        w.transient(app.root)
        w.bind("<Escape>", lambda e: w.destroy())
        self.thumbs = ThumbCache(app, px(40))
        self.arts = {}

        head = tk.Frame(w, bg=BG)
        head.pack(fill="x", padx=px(20), pady=(px(16), px(4)))
        tk.Label(head, text="Want list", bg=BG, fg=TEXT, font=(FONT, 14, "bold")).pack(side="left")
        self.sub = tk.Label(w, bg=BG, fg=MUTED, font=(FONT, 9), anchor="w", justify="left", wraplength=px(560))
        self.sub.pack(fill="x", padx=px(20), pady=(0, px(10)))

        box = tk.Frame(w, bg=BG)
        box.pack(fill="both", expand=True, padx=(px(20), px(8)), pady=(0, px(16)))
        self.canvas = tk.Canvas(box, bg=BG, highlightthickness=0, width=px(560))
        bar = ttk.Scrollbar(box, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.body = tk.Frame(self.canvas, bg=BG)
        self.canvas.create_window((0, 0), window=self.body, anchor="nw", width=px(560))
        self.body.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        w.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-e.delta // 120, "units"))

        w.geometry(f"{px(610)}x{min(px(700), w.winfo_screenheight() - px(160))}")
        x = app.root.winfo_rootx() + (app.root.winfo_width() - px(610)) // 2
        w.geometry(f"+{max(0, x)}+{max(0, app.root.winfo_rooty() + px(30))}")
        self.load()

    # -- states

    def _clear(self):
        for child in self.body.winfo_children():
            child.destroy()
        self.arts = {}

    def _message(self, text, button=None, command=None):
        self._clear()
        tk.Label(self.body, text=text, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w", justify="left",
                 wraplength=self.app.px(540)).pack(fill="x", pady=(self.app.px(6), self.app.px(12)))
        if button:
            self.app.btn(self.body, button, command, kind="accent").pack(anchor="w")

    def load(self):
        cfg = self.app.core.config
        self.sub.config(text="Who in Vinyl Hangout has a record you're looking for, and who's looking for one you "
                             "have. It uses your Discogs want list.")
        if not cfg.get("discogs_username", "").strip():
            return self._message("This works with your Discogs username: set it in Settings → Records first.",
                                 "Open Settings", lambda: (self.win.destroy(), self.app.open_settings(start_tab=1)))
        if not cfg.get("hangout_matches"):
            return self._message("Switch this on to share your collection and your Discogs want list with Vinyl "
                                 "Hangout members who switched it on too. Nothing else is shared, and you can switch "
                                 "it off any time in Settings → Sharing & tray.", "Switch on want-list matches",
                                 self.switch_on)
        if self.app.core.discord.status.get("state") != "connected":
            return self._message("Vinyl Presence needs to be connected to Discord for this: other members see your "
                                 "Discord name next to your records. Start Discord and try again.")
        if not cfg.get("hangout_matches_published"):
            self._message("Getting your want list from Discogs and sharing it. This takes a moment…")
            self.app.core.hangout.shelf_wake.set()
            self.win.after(4000, lambda: self.win.winfo_exists() and self.load())
            return
        self._message("Looking for matches…")

        def work():
            try:
                data = self.app.core.hangout.matches()
            except (OSError, ValueError) as e:
                msg = f"Couldn't reach vinylhangout.com ({e}). Try again in a bit."
                self.app.ui.put(lambda: self.win.winfo_exists() and self._message(msg))
                return
            self.app.ui.put(lambda: self.win.winfo_exists() and self.show(data))
        threading.Thread(target=work, daemon=True, name="matches").start()

    def switch_on(self):
        self.app.core.update_config({"hangout_matches": True})
        self.load()

    def show(self, data):
        px = self.app.px
        self._clear()
        wants = len(self.app.core.hangout.wants or [])
        others = max(0, data.get("members", 1) - 1)
        self.sub.config(text=f"{wants} records on your Discogs want list · {others} other "
                             f"{'member shares' if others == 1 else 'members share'} theirs.")
        self._section("In the Hangout: records you're looking for", data["want_owned"], "Has it:",
                      "Nobody has a record from your want list yet. New members are matched as they join.")
        self._section("Wanted from you: records you have that members are looking for", data["have_wanted"],
                      "Wants it:", "Nobody is looking for a record you have yet.")
        tk.Label(self.body, text="Selling one? List it on Discogs, then post it in the server's buy & sell channel "
                                 "with /sell and the link. Buying and selling goes through Discogs, so everyone is "
                                 "protected.", bg=BG, fg=FAINT, font=(FONT, 9), anchor="w", justify="left",
                 wraplength=px(540)).pack(fill="x", pady=(px(14), 0))

    def _section(self, title, rows, who_label, empty):
        px = self.app.px
        tk.Label(self.body, text=title, bg=BG, fg=TEXT, font=(FONT, 11, "bold"), anchor="w").pack(
            fill="x", pady=(px(8), px(6)))
        box = tk.Frame(self.body, bg=PANEL)
        box.pack(fill="x")
        if not rows:
            tk.Label(box, text=empty, bg=PANEL, fg=FAINT, font=(FONT, 10), anchor="w").pack(
                fill="x", padx=px(14), pady=px(12))
            return
        for r in rows:
            row = tk.Frame(box, bg=PANEL)
            row.pack(fill="x", padx=px(12), pady=px(6))
            art = tk.Label(row, bg=PANEL, cursor="hand2",
                           image=self.thumbs.get(r["release"], self._cover_ready, url=r.get("cover"))
                           or self.thumbs.placeholder or "")
            art.pack(side="left", padx=(0, px(12)))
            self.arts[r["release"]] = art
            text = tk.Frame(row, bg=PANEL)
            text.pack(side="left", fill="x", expand=True)
            title = tk.Label(text, text=r["title"], bg=PANEL, fg=TEXT, font=(FONT, 10, "bold"), anchor="w",
                             cursor="hand2")
            title.pack(fill="x")
            tk.Label(text, text=r["artist"], bg=PANEL, fg=MUTED, font=(FONT, 9), anchor="w").pack(fill="x")
            url = f"https://www.discogs.com/release/{r['release']}"
            for widget in (art, title):
                widget.bind("<Button-1>", lambda e, url=url: webbrowser.open(url))
            people = tk.Frame(text, bg=PANEL)
            people.pack(fill="x")
            tk.Label(people, text=who_label, bg=PANEL, fg=FAINT, font=(FONT, 9)).pack(side="left")
            for i, p in enumerate(r["people"]):
                name = tk.Label(people, text=p["name"] + ("" if i == len(r["people"]) - 1 else ","), bg=PANEL,
                                fg=ACCENT if p.get("slug") else TEXT, font=(FONT, 9),
                                cursor="hand2" if p.get("slug") else "")
                name.pack(side="left", padx=(px(4), 0))
                if p.get("slug"):  # their member page
                    name.bind("<Button-1>", lambda e, s=p["slug"]: webbrowser.open(f"https://vinylhangout.com/u/{s}"))
                if not p.get("exact"):
                    tk.Label(people, text="(another pressing)", bg=PANEL, fg=FAINT, font=(FONT, 8)).pack(side="left")

    def _cover_ready(self, rid):
        art = self.arts.get(rid)
        if art is not None and art.winfo_exists():
            art.config(image=self.thumbs.images[rid])
