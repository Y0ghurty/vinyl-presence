"""Collection, settings, history and the player that drives Discord Rich Presence.

No UI code in here: the window in app.py just reads Player.snapshot() and calls
Player.play()/control(). All network/Discord work happens on background threads.
"""
import csv
import io
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from discord_ipc import PresenceWorker
from metadata import USER_AGENT, Library, clean_name, norm

SOURCE_DIR = Path(__file__).resolve().parent
# Bundled files. Inside the .exe, PyInstaller unpacks them to a temporary folder (_MEIPASS).
ASSETS = Path(getattr(sys, "_MEIPASS", SOURCE_DIR)) / "assets"
# Settings, history and caches: next to the code when run from source, in
# %APPDATA%\Vinyl Presence for the .exe. VINYL_PRESENCE_HOME overrides both.
if os.environ.get("VINYL_PRESENCE_HOME"):
    ROOT = Path(os.environ["VINYL_PRESENCE_HOME"])
elif getattr(sys, "frozen", False):
    ROOT = Path(os.environ.get("APPDATA") or Path.home()) / "Vinyl Presence"
else:
    ROOT = SOURCE_DIR
DATA = ROOT / "data"
CONFIG_PATH = ROOT / "config.json"
HISTORY_PATH = DATA / "history.json"
IMPORTED_CSV = DATA / "collection.csv"

DEFAULT_CONFIG = {
    "discord_client_id": "",
    "discogs_token": "",
    # Title of the status card ("Listening to ___"): "app" (the name of your Discord application,
    # e.g. "My Record Player"), "artist", "album" or "custom" (card_title_text)
    "card_title": "app",
    "card_title_text": "Vinyl",
    # What the member list shows after "Listening to": "artist", "title" or "app" (the card title)
    "status_display": "artist",
    # Name of the Rich Presence art asset you uploaded (assets/vinyl.png). Leave empty if you didn't.
    "vinyl_asset": "vinyl",
    "show_discogs_button": True,
    # When a side ends: False = wait for you to pick the next side, True = flip automatically
    "auto_continue": False,
    "flip_seconds": 30,
    # Clear the status after this long when track lengths are unknown and you don't touch anything
    "auto_stop_minutes": 60,
    "list_sort": "Artist A–Z",
}
SIDE_END_CLEAR_MINUTES = 10
STATUS_DISPLAY_TYPES = {"app": 0, "artist": 1, "title": 2}
# First word of the Discogs format -> (how to say it, what it's played on)
MEDIA = {
    "vinyl": ("vinyl", "Spinning on my turntable"),
    "shellac": ("shellac", "Spinning on my turntable"),
    "cd": ("CD", "Playing on my CD player"),
    "cassette": ("cassette", "Playing on my tape deck"),
}


def medium(rec):
    return MEDIA.get((rec.get("format") or "Vinyl").split(",")[0].strip().lower(), MEDIA["vinyl"])


def now_ms():
    return int(time.time() * 1000)


def load_json(path, default):
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(path)


def clip(s, limit=128):
    """Discord wants 2..128 characters for text fields."""
    s = (s or "").strip()
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s if len(s) >= 2 else (s + "⠀" if s else None)


# ---------------------------------------------------------------- collection

def parse_collection(text):
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    fields = set(reader.fieldnames or [])
    if not {"release_id", "Artist", "Title"} <= fields:
        raise ValueError("This doesn't look like a Discogs collection export "
                         "(expected columns release_id, Artist, Title).")
    records = {}
    for row in reader:
        rid = (row.get("release_id") or "").strip()
        if not rid.isdigit():
            continue
        year = (row.get("Released") or "").strip()[:4]
        rec = {
            "id": int(rid),
            "artist": clean_name(row.get("Artist")),
            "title": (row.get("Title") or "").strip(),
            "label": clean_name(row.get("Label")),
            "catno": (row.get("Catalog#") or "").strip(),
            "format": (row.get("Format") or "").strip(),
            "year": year if year.isdigit() and year != "0" else "",
            "added": (row.get("Date Added") or "").strip(),
        }
        # Pre-normalized fields for fast, accent-insensitive search
        rec["_a"], rec["_t"] = norm(rec["artist"]), norm(rec["title"])
        rec["_h"] = norm(" ".join(rec[k] for k in ("artist", "title", "label", "catno", "year", "format")))
        rec["_w"] = rec["_h"].split()
        records[rid] = rec
    return records


def find_collection_file():
    candidates = list(ROOT.glob("*.csv"))
    if IMPORTED_CSV.exists():
        candidates.append(IMPORTED_CSV)
    return max(candidates, key=lambda p: p.stat().st_mtime, default=None)


def _subsequence(needle, word):
    it = iter(word)
    return all(c in it for c in needle)


def search(records, query):
    """Every word must match (artist/title/label/cat#/year/format). Forgives small typos."""
    full = norm(query)
    toks = full.split()
    scored = []
    for r in records:
        score = 0.0
        for t in toks:
            if t in r["_h"]:
                score += 2
                if r["_a"].startswith(t) or r["_t"].startswith(t):
                    score += 4
                elif f" {t}" in f" {r['_a']} {r['_t']}":
                    score += 2
            elif len(t) >= 3 and any(w[0] == t[0] and len(w) >= len(t) and _subsequence(t, w) for w in r["_w"]):
                score += 0.5  # "zepelin" still finds "Zeppelin"
            else:
                break
        else:
            if full in (r["_t"], r["_a"]):
                score += 10
            elif r["_t"].startswith(full) or r["_a"].startswith(full):
                score += 5
            scored.append((-score, r["_a"], r["_t"], r))
    scored.sort(key=lambda x: x[:3])
    return [x[3] for x in scored]


# ---------------------------------------------------------------- app state

class Core:
    def __init__(self):
        DATA.mkdir(parents=True, exist_ok=True)
        self.config = {**DEFAULT_CONFIG, **load_json(CONFIG_PATH, {})}
        if not CONFIG_PATH.exists():
            save_json(CONFIG_PATH, self.config)
        self.history = load_json(HISTORY_PATH, [])
        self.records, self.collection_file = {}, None
        self.library = Library(DATA / "releases.json", lambda: self.config.get("discogs_token", "").strip())
        self.discord = PresenceWorker()
        self.discord.set_client_id(self.config["discord_client_id"])
        self.discord.start()
        self.app_assets = None  # art assets uploaded to the Discord app (None = not checked yet)
        self._assets_wake = threading.Event()
        self.player = Player(self)
        threading.Thread(target=self._watch_assets, daemon=True, name="assets").start()
        self.load_collection()

    def _watch_assets(self):
        """Check which images are uploaded to the Discord app, so we never point at a missing badge."""
        checked = None
        while True:
            cid, want = self.config["discord_client_id"], self.config["vinyl_asset"].strip()
            if cid and (cid != checked or (want and want not in (self.app_assets or ()))):
                url = f"https://discord.com/api/v10/oauth2/applications/{cid}/assets"
                try:
                    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}),
                                                timeout=10) as resp:
                        names = {a["name"] for a in json.load(resp)}
                    checked = cid
                    if names != self.app_assets:
                        self.app_assets = names
                        self.player.push()
                except (OSError, ValueError, KeyError, TypeError):
                    pass
            self._assets_wake.wait(120)
            self._assets_wake.clear()

    def has_badge(self):
        want = self.config["vinyl_asset"].strip()
        return bool(want) and (self.app_assets is None or want in self.app_assets)

    def load_collection(self):
        path = find_collection_file()
        if not path:
            print("[collection] no CSV yet")
            return
        try:
            self.records = parse_collection(path.read_text("utf-8-sig", errors="replace"))
            self.collection_file = path.name
            print(f"[collection] {len(self.records)} records from {path.name}")
        except ValueError as e:
            print(f"[collection] {path.name}: {e}")
            return
        recent = {h["id"] for h in self.history}
        order = sorted(self.records.values(), key=lambda r: (r["id"] not in recent, r["_a"]))
        self.library.prefetch(order)

    def import_csv(self, text):
        records = parse_collection(text)
        if not records:
            raise ValueError("No records found in that file.")
        IMPORTED_CSV.write_text(text.lstrip("﻿"), "utf-8")
        self.load_collection()
        return len(self.records)

    def update_config(self, changes):
        token_changed = changes.get("discogs_token", self.config["discogs_token"]) != self.config["discogs_token"]
        for key, value in changes.items():
            if key in DEFAULT_CONFIG:
                self.config[key] = type(DEFAULT_CONFIG[key])(value)
        save_json(CONFIG_PATH, self.config)
        self.discord.set_client_id(self.config["discord_client_id"])
        self._assets_wake.set()
        if token_changed and self.config["discogs_token"]:
            self.library.prefetch(list(self.records.values()))
        self.player.push()

    def add_history(self, rid):
        self.history = [{"id": int(rid), "at": int(time.time())}] + self.history[:499]
        save_json(HISTORY_PATH, self.history)

    def play_stats(self):
        plays, last = {}, {}
        for h in self.history:
            plays[h["id"]] = plays.get(h["id"], 0) + 1
            last.setdefault(h["id"], h["at"])
        return plays, last

    def shutdown(self):
        self.library.save()
        self.discord.shutdown()


class Player:
    """What's on the platter, which side/track, and the matching Discord activity."""

    def __init__(self, core):
        self.core = core
        self.lock = threading.RLock()
        self.now = None
        threading.Thread(target=self._ticker, daemon=True, name="ticker").start()

    # -- actions (safe to call from any thread; play() may block on a network lookup)

    def play(self, rid, side=None, track=None):
        rec = self.core.records.get(str(rid))
        if not rec:
            raise KeyError("Record not in your collection")
        info = self.core.library.get(rec) or {}
        sides = {}
        for t in info.get("tracks") or []:
            sides.setdefault(t["side"], []).append(t)
        with self.lock:
            same = bool(self.now) and self.now["record"]["id"] == rec["id"]
            t = now_ms()
            if side not in sides:
                side = next(iter(sides), None)
            tracks = sides.get(side) or []
            idx = min(max(int(track or 0), 0), len(tracks) - 1) if tracks else None
            self.now = {
                "record": rec, "info": info, "sides": sides, "side": side, "idx": idx,
                "manual": track is not None,
                "started": self.now["started"] if same else t,
                "track_started": t, "side_done": False, "side_done_at": None, "touched": t,
            }
        if not same:
            self.core.add_history(rec["id"])
        self.push()

    def control(self, action, side=None):
        if action == "stop":
            return self.stop()
        with self.lock:
            n = self.now
            if not n:
                return
            if action == "side":
                rid = n["record"]["id"]
            else:
                tracks = n["sides"].get(n["side"]) or []
                if not tracks:
                    return
                t = now_ms()
                idx = len(tracks) if n["side_done"] else (n["idx"] or 0)
                if action == "next":
                    idx += 1
                elif action == "prev" and (n["side_done"] or t - n["track_started"] < 4000 or not self._track_mode(n)):
                    idx -= 1
                n["manual"] = n["manual"] or not self._timed(n)
                n["touched"] = t
                if idx >= len(tracks):
                    n["side_done"], n["side_done_at"] = True, t
                else:
                    n["idx"], n["track_started"], n["side_done"] = max(idx, 0), t, False
        if action == "side":
            return self.play(rid, side)
        self.push()

    def stop(self):
        with self.lock:
            self.now = None
        self.push()

    # -- state helpers

    @staticmethod
    def _timed(n):
        tracks = n["sides"].get(n["side"]) or []
        return bool(tracks) and all(t["duration"] for t in tracks)

    def _track_mode(self, n):
        return n["idx"] is not None and not n["side_done"] and (self._timed(n) or n["manual"])

    @staticmethod
    def _next_side(n):
        names = list(n["sides"])
        if n["side"] in names and names.index(n["side"]) + 1 < len(names):
            return names[names.index(n["side"]) + 1]
        return None

    def _ticker(self):
        while True:
            time.sleep(1)
            try:
                self._tick()
            except Exception as e:  # keep ticking no matter what
                print(f"[player] {e}")

    def _tick(self):
        changed, flip_to, stop = False, None, False
        cfg = self.core.config
        with self.lock:
            n = self.now
            if not n:
                return
            t = now_ms()
            tracks = n["sides"].get(n["side"]) or []
            if self._timed(n) and not n["side_done"] and n["idx"] is not None:
                # Advance through tracks by their lengths (several at once if the PC slept)
                while not n["side_done"]:
                    end = n["track_started"] + tracks[n["idx"]]["duration"] * 1000
                    if t < end:
                        break
                    changed = True
                    if n["idx"] + 1 < len(tracks):
                        n["idx"] += 1
                        n["track_started"] = end
                    else:
                        n["side_done"], n["side_done_at"] = True, end
            if n["side_done"]:
                waited = t - n["side_done_at"]
                nxt = self._next_side(n)
                if cfg.get("auto_continue") and nxt and waited >= cfg.get("flip_seconds", 30) * 1000:
                    flip_to = (n["record"]["id"], nxt)
                elif waited >= SIDE_END_CLEAR_MINUTES * 60000:
                    stop = True
            elif not self._timed(n) and t - n["touched"] >= cfg.get("auto_stop_minutes", 60) * 60000:
                stop = True
        if flip_to:
            self.play(*flip_to)
        elif stop:
            print("[player] nothing touched for a while, clearing status")
            self.stop()
        elif changed:
            self.push()

    # -- outputs

    def push(self):
        self.core.discord.set_activity(self.activity())

    def activity(self):
        cfg = self.core.config
        with self.lock:
            n = self.now
            if not n:
                return None
            rec, info = n["record"], n["info"]
            tracks = n["sides"].get(n["side"]) or []
            side_label = f"Side {n['side']}" if n["side"] and len(n["side"]) <= 2 else (n["side"] or "")
            cover = info.get("cover")
            if cover and len(cover) > 256:
                cover = info.get("thumb") if len(info.get("thumb") or "") <= 256 else None
            vinyl = cfg["vinyl_asset"].strip() if self.core.has_badge() else ""
            on_medium, played_on = medium(rec)
            on_medium = f"on {on_medium}"
            act = {
                "type": 2,  # "Listening to"
                "status_display_type": STATUS_DISPLAY_TYPES.get(cfg.get("status_display"), 1),
                "assets": {},
                "instance": False,
            }
            url = f"https://www.discogs.com/release/{rec['id']}"
            if self._track_mode(n):
                tr = tracks[n["idx"]]
                act["details"] = clip(tr["title"])
                act["state"] = clip(tr.get("artist") or rec["artist"])
                large_text = " · ".join(x for x in (rec["title"], side_label, on_medium) if x)
                start = n["track_started"]
                act["timestamps"] = {"start": start}
                if tr["duration"]:
                    act["timestamps"]["end"] = start + tr["duration"] * 1000
            else:
                act["details"] = clip(rec["title"])
                act["state"] = clip(rec["artist"])
                if n["side_done"]:
                    nxt = self._next_side(n)
                    large_text = f"{side_label} finished · flipping to Side {nxt}" if nxt else "Record finished"
                else:
                    large_text = " · ".join(x for x in (side_label, on_medium, rec["year"]) if x)
                    act["timestamps"] = {"start": n["track_started"] if n["side"] else n["started"]}
            card_title = {"artist": act["state"], "album": rec["title"],
                          "custom": cfg.get("card_title_text")}.get(cfg.get("card_title"))
            if clip(card_title):
                act["name"] = clip(card_title)  # replaces the app name in "Listening to ___"
            act["details_url"] = url
            if info.get("artist_id"):
                act["state_url"] = f"https://www.discogs.com/artist/{info['artist_id']}"
            if cover:
                act["assets"]["large_image"] = cover
                act["assets"]["large_url"] = url
                if vinyl:
                    act["assets"]["small_image"] = vinyl
                    act["assets"]["small_text"] = played_on
            elif vinyl:
                act["assets"]["large_image"] = vinyl
            if act["assets"].get("large_image"):
                act["assets"]["large_text"] = clip(large_text or rec["title"])
            if not act["assets"]:
                del act["assets"]
            if cfg.get("show_discogs_button", True):
                act["buttons"] = [{"label": "View on Discogs", "url": url}]
            return {k: v for k, v in act.items() if v is not None}

    def snapshot(self):
        with self.lock:
            n = self.now
            if not n:
                return None
            tracks = n["sides"].get(n["side"]) or []
            info = n["info"]
            return {
                "record": n["record"],
                "cover": info.get("cover") or info.get("thumb"),
                "sides": list(n["sides"]),
                "side": n["side"],
                "tracks": tracks,
                "idx": n["idx"] if self._track_mode(n) else None,
                "timed": self._timed(n),
                "track_started": n["track_started"],
                "started": n["started"],
                "side_done": n["side_done"],
                "next_side": self._next_side(n),
            }
