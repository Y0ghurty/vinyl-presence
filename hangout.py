"""vinylhangout.com: the user count and the Live page.

Two things, each with its own switch in Settings → Sharing & tray:
- Count me (on by default): about once a day the app says "I'm here" with a random ID, so the site
  can count how many people use Vinyl Presence. Nothing else is sent.
- Show me on the Live page (off by default): while a record plays, the site shows it with your
  Discord name and picture. It disappears when you stop.
"""
import json
import os
import threading
import time
import urllib.request

from metadata import USER_AGENT, side_name
from version import VERSION

API = os.environ.get("VINYL_HANGOUT_API", "https://vinylhangout.com/api")
HELLO_EVERY = 20 * 3600
LIVE_AFTER_SECONDS = 20   # a mis-click never shows up
HEARTBEAT_SECONDS = 240   # the site drops a record after 10 minutes without news
RETRY_SECONDS = 120       # when the site can't be reached


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
        self.wake = threading.Event()
        threading.Thread(target=self._loop, daemon=True, name="hangout").start()

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
        if not cfg.get("hangout_live") or not name:
            return None
        with player.lock:
            n = player.now
            if not n or n["listened"] < LIVE_AFTER_SECONDS:
                return None
            rec, info = n["record"], n["info"]
            tracks = n["sides"].get(n["side"]) or []
            track = tracks[n["idx"]]["title"] if player._track_mode(n) else ""
            key = (rec["id"], n["side"], track)
            payload = {
                "name": name, "avatar": self.core.discord.status.get("avatar") or "",
                "release": int(rec["id"]), "title": rec["title"], "artist": rec["artist"], "year": rec.get("year") or "",
                "cover": info.get("thumb") or info.get("cover") or "", "side": side_name(n["side"]) or "",
                "track": track, "started": int(n["started"] / 1000),
            }
        return key, payload

    def _live(self):
        now = self._now()
        key = now[0] if now else None
        if key == self._sent and (not key or time.time() - self._sent_at < HEARTBEAT_SECONDS):
            return
        if now:
            _post("spin.php", {"id": self.core.config["install_id"], "action": "play", **now[1]})
        else:
            _post("spin.php", {"id": self.core.config["install_id"], "action": "stop"})
        self._sent, self._sent_at = key, time.time()

    def stop(self):
        """Take us off the Live page right away (the app is closing)."""
        if self._sent:
            try:
                _post("spin.php", {"id": self.core.config["install_id"], "action": "stop"})
            except (OSError, ValueError):
                pass  # the site drops us by itself after a few minutes
            self._sent = None
