"""vinylhangout.com: the user count and the Live page.

Each with its own switch in Settings → Sharing & tray:
- Count me (on by default): about once a day the app says "I'm here" with a random ID, so the site
  can count how many people use Vinyl Presence. Nothing else is sent.
- Show me on the Live page (off by default): while a record plays, the site shows it with your
  Discord name and picture. It disappears when you stop.
- Post my records in the Vinyl Hangout Discord (off by default): the site posts each record you put on
  in the server's now-spinning channel, also when the server owner's PC is off.
- My member page (off by default): vinylhangout.com/u/<page name> shows your Discord name and picture,
  this month's listening stats, your top records and artists, and what you spun recently.
- Want-list matches (off by default): your collection (release numbers) and your Discogs want list, so members
  see who in the Hangout has a record they're looking for, and who's looking for one you have.
"""
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request

import achievements
import stats
from metadata import USER_AGENT, RateLimited, fetch_wantlist, side_name
from version import VERSION

API = os.environ.get("VINYL_HANGOUT_API", "https://vinylhangout.com/api")
HELLO_EVERY = 20 * 3600
LIVE_AFTER_SECONDS = 20   # a mis-click never shows up
HEARTBEAT_SECONDS = 240   # the site drops a record after 10 minutes without news
RETRY_SECONDS = 120       # when the site can't be reached
PAGE_EVERY = 3600         # the member page is refreshed at least this often (and whenever a record starts)
SHELF_EVERY = 6 * 3600   # the want list is fetched again, and the shelf resent, this often
PAGE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,22}[a-z0-9]$")


def page_name_from(name):
    """A page name made from a Discord name: "DJ Yogurty!" -> "dj-yogurty"."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", (name or "").lower()).strip("-_")[:24].strip("-_")
    return slug if PAGE_NAME_RE.match(slug) else ""


def _error(e):
    """The site's own explanation of a refused request, like "That page name is taken"."""
    if isinstance(e, urllib.error.HTTPError):
        try:
            return json.load(e).get("error") or str(e)
        except ValueError:
            pass
    return str(e)


def _post(path, payload):
    req = urllib.request.Request(f"{API}/{path}", data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


class Hangout:
    def __init__(self, core):
        self.core = core
        self._hello_at = 0
        self._sent = None  # (record id, side, track) the site last heard about, None when it shows nothing
        self._sent_at = 0
        self._retry_at = 0
        self._page_key = None  # what the member page was last made from
        self._page_at = 0
        self._page_lock = threading.Lock()
        self.page_error = None  # why the site refused the page, e.g. a taken name
        self.wake = threading.Event()
        self.page_wake = threading.Event()
        self.shelf_wake = threading.Event()
        self.wants = None  # your Discogs want list, once fetched
        self._wants_for, self._wants_at = None, 0
        self._shelf_key, self._shelf_at = None, 0
        threading.Thread(target=self._loop, daemon=True, name="hangout").start()
        threading.Thread(target=self._shelf_loop, daemon=True, name="want-list").start()
        # The member page has its own thread, so making it can never hold up reporting a record
        threading.Thread(target=self._page_loop, daemon=True, name="member-page").start()

    def poke(self):
        """Settings changed: check right away instead of at the next tick."""
        self.wake.set()
        self.page_wake.set()
        self.shelf_wake.set()

    def _page_loop(self):
        time.sleep(15)
        while True:
            try:
                self._page()
            except (OSError, ValueError) as e:
                print(f"[hangout] member page: {e}")
                self.page_wake.wait(RETRY_SECONDS)
            self.page_wake.wait(10)
            self.page_wake.clear()

    def _loop(self):
        time.sleep(10)  # let the app start first
        while True:
            if time.time() >= self._retry_at:
                try:
                    self._hello()
                    self._live()
                except (OSError, ValueError) as e:
                    print(f"[hangout] {e}")
                    self._retry_at = time.time() + RETRY_SECONDS
            self.wake.wait(5)
            self.wake.clear()

    def _hello(self):
        if not self.core.config.get("hangout_count", True) or time.time() - self._hello_at < HELLO_EVERY:
            return
        _post("hello.php", {"id": self.core.config["install_id"], "v": VERSION})
        self._hello_at = time.time()

    def _now(self):
        """What to show on the Live page right now, or None."""
        cfg, player = self.core.config, self.core.player
        name = self.core.discord.status.get("user") if self.core.discord.status.get("state") == "connected" else None
        live, post = bool(cfg.get("hangout_live")), bool(cfg.get("hangout_post"))
        if not (live or post or cfg.get("hangout_page")) or not name:
            return None
        with player.lock:
            n = player.now
            if not n or n["listened"] < LIVE_AFTER_SECONDS:
                return None
            rec, info = n["record"], n["info"]
            tracks = n["sides"].get(n["side"]) or []
            track = tracks[n["idx"]]["title"] if player._track_mode(n) else ""
            key = (rec["id"], n["side"], track, live, post)
            fmt = rec.get("format") or ""
            payload = {
                "live": live, "post": post,
                "name": name, "avatar": self.core.discord.status.get("avatar") or "",
                "release": int(rec["id"]), "title": rec["title"], "artist": rec["artist"], "year": rec.get("year") or "",
                "label": rec.get("label") or "", "format": fmt[7:] if fmt.startswith("Vinyl, ") else fmt,
                "cover": info.get("thumb") or info.get("cover") or "",  # small, for the Live page
                "image": info.get("cover") or info.get("thumb") or "",  # big, for the Discord post
                "side": side_name(n["side"]) or "", "track": track, "started": int(n["started"] / 1000),
            }
        return key, payload

    def _live(self):
        now = self._now()
        key = now[0] if now else None
        if key == self._sent and (not key or time.time() - self._sent_at < HEARTBEAT_SECONDS):
            return
        if now:
            started = time.time()
            _post("spin.php", {"id": self.core.config["install_id"], "action": "play", **now[1]})
            r = now[1]
            print(f"[hangout] {time.strftime('%H:%M:%S')} sent {r['artist']} - {r['title']} "
                  f"({' · '.join(x for x in (r['side'], r['track']) if x)}) in {time.time() - started:.1f} s")
        else:
            _post("spin.php", {"id": self.core.config["install_id"], "action": "stop"})
        self._sent, self._sent_at = key, time.time()

    # -- the member page

    def _page(self):
        cfg = self.core.config
        if not cfg.get("hangout_page") or not cfg.get("hangout_page_name"):
            if cfg.get("hangout_page_published"):
                _post("profile.php", {"id": cfg["install_id"], "action": "delete"})
                self.core.update_config({"hangout_page_published": False})
                print("[hangout] member page removed")
            self._page_key = None
            return
        key = self._page_source()
        if key and (key != self._page_key or time.time() - self._page_at >= PAGE_EVERY):
            self.publish_page()

    def _page_source(self):
        """Changes whenever the page should be made again: a new name, picture or record. None without Discord."""
        status = self.core.discord.status
        if status.get("state") != "connected" or not status.get("user"):
            return None
        newest = self.core.history[0]["at"] if self.core.history else 0
        return self.core.config.get("hangout_page_name"), status["user"], status.get("avatar"), newest

    def publish_page(self):
        """Send the member page now. Returns None, or why it didn't work (raises OSError when the site is down)."""
        with self._page_lock:
            cfg = self.core.config
            key = self._page_source()
            if not key:
                return "Vinyl Presence isn't connected to Discord, so it doesn't know your name yet."
            payload = {"id": cfg["install_id"], "slug": cfg["hangout_page_name"], "name": key[1],
                       "avatar": key[2] or "", **self._snapshot()}
            self._page_key, self._page_at = key, time.time()  # a refused name isn't retried until something changes
            try:
                started = time.time()
                _post("profile.php", payload)
                print(f"[hangout] {time.strftime('%H:%M:%S')} member page updated in {time.time() - started:.1f} s")
            except urllib.error.HTTPError as e:
                self.page_error = _error(e)
                print(f"[hangout] member page refused: {self.page_error}")
                return self.page_error
            self.page_error = None
            if not cfg.get("hangout_page_published"):
                self.core.update_config({"hangout_page_published": True})
            return None

    def _snapshot(self):
        core = self.core

        def entry(rec, plays=0, secs=0, at=0):
            info = core.library.get_cached(rec["id"]) or {}
            return {"release": int(rec["id"]), "title": rec["title"], "artist": rec["artist"],
                    "cover": info.get("thumb") or info.get("cover") or "", "plays": plays, "secs": int(secs), "at": at}

        month = stats.compute(core, "This month")
        total = stats.compute(core, "All time")
        recent, last = [], None
        for h, secs in reversed(stats.sessions(core)):  # newest first; the same record twice in a row once
            rec = core.records.get(str(h["id"]))
            if not rec or secs < stats.MIN_PLAY_SECONDS or h["id"] == last:
                continue
            last = h["id"]
            recent.append(entry(rec, at=h["at"]))
            if len(recent) == 12:
                break
        return {
            "stats": {
                "month": time.strftime("%B"),
                "hours_month": month["seconds"] / 3600, "plays_month": month["sessions"],
                "hours_total": total["seconds"] / 3600, "collection": len(core.records),
                "artists": [name for name, _plays, _secs in month["top_artists"][:5]],
                "most_played": [entry(r, n, s) for r, n, s in month["top_records"][:5]],
                "most_time": [entry(r, n, s) for r, n, s in month["top_time"][:5]],
            },
            "recent": recent,
            "achievements": achievements.for_page(core),
            "achievements_total": len(achievements.CATALOG),
        }

    # -- want-list matches

    def _shelf_loop(self):
        time.sleep(20)
        while True:
            try:
                self._shelf()
            except (OSError, ValueError, RateLimited) as e:
                print(f"[hangout] want list: {e}")
                self.shelf_wake.wait(600)
            self.shelf_wake.wait(600)
            self.shelf_wake.clear()

    def _shelf(self):
        cfg, core = self.core.config, self.core
        user = cfg.get("discogs_username", "").strip()
        status = core.discord.status
        if not (cfg.get("hangout_matches") and user):
            if cfg.get("hangout_matches_published"):
                _post("shelf.php", {"id": cfg["install_id"], "action": "delete"})
                core.update_config({"hangout_matches_published": False})
                print("[hangout] want list removed from vinylhangout.com")
            return
        if status.get("state") != "connected" or not status.get("user"):
            return  # the name shown to other members comes from Discord
        if user != self._wants_for or time.time() - self._wants_at >= SHELF_EVERY:
            while core.library.pending():  # record info first: they share Discogs' rate limit
                time.sleep(10)
            self.wants = fetch_wantlist(user, cfg.get("discogs_token", "").strip() or None)
            self._wants_for, self._wants_at = user, time.time()
        key = (core.collection_version, user, len(self.wants), status["user"], status.get("id"))
        if key == self._shelf_key and time.time() - self._shelf_at < SHELF_EVERY:
            return
        have = [{"r": int(r["id"]), "m": int(r.get("master") or 0)} for r in list(core.records.values())]
        _post("shelf.php", {"id": cfg["install_id"], "name": status["user"], "discord_id": status.get("id") or "",
                            "have": have, "want": self.wants})
        self._shelf_key, self._shelf_at = key, time.time()
        print(f"[hangout] {time.strftime('%H:%M:%S')} shared {len(have)} records and {len(self.wants)} wants for matches")
        if not cfg.get("hangout_matches_published"):
            core.update_config({"hangout_matches_published": True})

    def matches(self):
        """Your matches from vinylhangout.com: {"want_owned", "have_wanted", "members"}. Raises OSError offline."""
        return _post("matches.php", {"id": self.core.config["install_id"]})

    def stop(self):
        """Take us off the Live page right away (the app is closing)."""
        if self._sent:
            try:
                _post("spin.php", {"id": self.core.config["install_id"], "action": "stop"})
            except (OSError, ValueError):
                pass  # the site drops us by itself after a few minutes
            self._sent = None
