"""Release details (cover art + tracklist with lengths) from Discogs, with Deezer as backup.

Discogs gives the exact cover and tracklist for your pressing, but vinyl
tracklists often have no track lengths. Deezer fills those in (and supplies
a cover if Discogs has none), which is what makes the progress bar possible.
Everything is cached in data/releases.json so each record is looked up once.
"""
import json
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

USER_AGENT = "VinylPresence/1.0 (+local Discord Rich Presence app)"
DISCOGS = "https://api.discogs.com"
DEEZER = "https://api.deezer.com"
CACHE_VERSION = 2


class RateLimited(Exception):
    pass


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("&", " and ")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def norm_core(s):
    """Normalized title without '(Remastered 2011)', '[Live]', ' - 2011 Remaster' etc."""
    s = re.sub(r"[\(\[].*?[\)\]]", " ", s or "")
    s = re.sub(r"\s+-\s+.*$", " ", s)
    return norm(s)


def clean_name(s):
    """Discogs artist/label names: 'Prince (2)' -> 'Prince', 'Bowie*' -> 'Bowie'."""
    s = re.sub(r"\s\(\d+\)", "", s or "")
    return re.sub(r"\*(?=$|[\s,])", "", s).strip()


def parse_duration(s):
    parts = (s or "").strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    total = 0
    for n in nums:
        total = total * 60 + n
    return total or None


def side_of(position):
    pos = (position or "").strip()
    m = re.match(r"^([A-Za-z]+)", pos)
    if m:
        return m.group(1).upper()
    m = re.match(r"^(?:CD)?(\d+)[-.]\d+", pos, re.I)
    if m:
        return f"Disc {m.group(1)}"
    return "All"


def _get_json(url, headers=None, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise RateLimited(url) from e
        raise


def _artist_names(artists):
    artists = artists or []
    parts = []
    for i, a in enumerate(artists):
        parts.append(clean_name(a.get("name") or ""))
        if i < len(artists) - 1:
            join = (a.get("join") or ",").strip()
            parts.append(", " if join == "," else f" {join} ")
    return "".join(parts).strip()


def fetch_discogs(release_id, token=None):
    headers = {"Authorization": f"Discogs token={token}"} if token else {}
    d = _get_json(f"{DISCOGS}/releases/{release_id}", headers)
    images = d.get("images") or []
    primary = next((i for i in images if i.get("type") == "primary"), images[0] if images else {})
    tracks = []
    for t in d.get("tracklist") or []:
        items = t.get("sub_tracks") if t.get("type_") == "index" and t.get("sub_tracks") else [t]
        for st in items:
            if st.get("type_", "track") != "track":
                continue
            tracks.append({
                "pos": (st.get("position") or "").strip(),
                "title": (st.get("title") or "").strip() or "Untitled",
                "artist": _artist_names(st.get("artists")),
                "duration": parse_duration(st.get("duration")),
            })
    for t in tracks:
        t["side"] = side_of(t["pos"])
    artists = d.get("artists") or []
    return {
        "artist": _artist_names(artists),
        "artist_id": artists[0].get("id") if len(artists) == 1 else None,
        "cover": primary.get("uri") or None,
        "thumb": primary.get("uri150") or d.get("thumb") or None,
        "tracks": tracks,
        "year": d.get("year") or None,
    }


def fetch_deezer(artist, title):
    """Find the album on Deezer; returns {'cover', 'thumb', 'tracks': [(title, seconds)]} or None."""
    artist_q = "" if norm(artist) in ("various", "various artists") else artist
    queries = []
    if artist_q:
        queries.append(f'artist:"{artist_q}" album:"{title}"')
        queries.append(f"{artist_q} {title}")
    queries.append(title)
    want_title, want_artist = norm_core(title), norm(artist_q)
    for q in queries:
        data = _get_json(f"{DEEZER}/search/album?" + urllib.parse.urlencode({"q": q, "limit": 10}))
        for alb in data.get("data") or []:
            got_title = norm_core(alb.get("title"))
            got_artist = norm((alb.get("artist") or {}).get("name"))
            title_ok = got_title == want_title or (
                min(len(got_title), len(want_title)) >= 4 and (got_title in want_title or want_title in got_title))
            artist_ok = not want_artist or got_artist == want_artist or (
                got_artist and (got_artist in want_artist or want_artist in got_artist))
            if title_ok and artist_ok:
                tr = _get_json(f"{DEEZER}/album/{alb['id']}/tracks?limit=200")
                return {
                    "cover": alb.get("cover_xl") or alb.get("cover_big"),
                    "thumb": alb.get("cover_medium"),
                    "tracks": [(t.get("title") or "", t.get("duration") or None) for t in tr.get("data") or []],
                }
    return None


def fill_durations(tracks, deezer_tracks):
    """Copy track lengths from Deezer onto Discogs tracks, matching by title."""
    if not deezer_tracks:
        return 0
    pool = [(norm(t), norm_core(t), d) for t, d in deezer_tracks if d]
    filled = 0
    for i, t in enumerate(tracks):
        if t["duration"]:
            continue
        a, ac = norm(t["title"]), norm_core(t["title"])
        match = None
        for full, core, dur in pool:
            if a == full or (ac and ac == core):
                match = dur
                break
        if match is None:
            for full, core, dur in pool:
                if min(len(ac), len(core)) >= 4 and (ac in core or core in ac):
                    match = dur
                    break
        if match is None and len(deezer_tracks) == len(tracks):
            match = deezer_tracks[i][1]
        if match:
            t["duration"] = match
            filled += 1
    return filled


class Library:
    """Thread-safe cache of release details plus a polite background prefetcher."""

    def __init__(self, cache_path, get_token):
        self.cache_path = cache_path
        self.get_token = get_token
        self.lock = threading.Lock()
        self.version = 0
        self.last_error = None
        self._fetch_locks = {}
        self._queue = deque()
        self._wake = threading.Event()
        self.cache = {}
        try:
            raw = json.loads(cache_path.read_text("utf-8"))
            if raw.get("_v") == CACHE_VERSION:
                self.cache = raw.get("releases", {})
        except (OSError, ValueError):
            pass
        self._dirty = 0
        threading.Thread(target=self._prefetch_loop, daemon=True, name="prefetch").start()

    def get_cached(self, rid):
        with self.lock:
            return self.cache.get(str(rid))

    def _needs_fetch(self, rid):
        info = self.get_cached(rid)
        if info is None:
            return True
        # A token added later can unlock covers that were missing before.
        return not info.get("cover") and not info.get("authed") and bool(self.get_token())

    def get(self, record, wait=True):
        rid = str(record["id"])
        if wait and self._needs_fetch(rid):
            try:
                self._fetch_once(record)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                print(f"[metadata] {record['artist']} - {record['title']}: {self.last_error}")
        return self.get_cached(rid)

    def _fetch_once(self, record):
        """Fetch unless another thread just did (UI click and prefetcher can race)."""
        rid = str(record["id"])
        with self.lock:
            flock = self._fetch_locks.setdefault(rid, threading.Lock())
        with flock:
            if self._needs_fetch(rid):
                self._fetch(record)

    def _fetch(self, record):
        rid = str(record["id"])
        token = self.get_token()
        info = {"cover": None, "thumb": None, "tracks": [], "artist_id": None,
                "authed": bool(token), "fetched": int(time.time())}
        try:
            try:
                info.update(fetch_discogs(rid, token))
            except urllib.error.HTTPError as e:
                if e.code != 401 or not token:
                    raise
                self.last_error = "Discogs rejected your token (401). Check it in Settings."
                info["authed"] = False
                info.update(fetch_discogs(rid))
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        need_lengths = any(not t["duration"] for t in info["tracks"])
        if need_lengths or not info["cover"]:
            try:
                dz = fetch_deezer(record["artist"], record["title"])
            except (urllib.error.URLError, ValueError, RateLimited):
                dz = None
            if dz:
                fill_durations(info["tracks"], dz["tracks"])
                if not info["tracks"]:
                    info["tracks"] = [{"pos": str(i + 1), "side": "All", "title": t, "artist": "", "duration": d}
                                      for i, (t, d) in enumerate(dz["tracks"])]
                if not info["cover"]:
                    info["cover"], info["thumb"] = dz["cover"], dz["thumb"]
        with self.lock:
            self.cache[rid] = info
            self.version += 1
            self._dirty += 1
        self.save(force=False)
        return info

    def save(self, force=True):
        with self.lock:
            if not force and self._dirty < 10:
                return
            self._dirty = 0
            data = json.dumps({"_v": CACHE_VERSION, "releases": self.cache}, ensure_ascii=False)
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(data, "utf-8")
        tmp.replace(self.cache_path)

    def prefetch(self, records):
        """Queue the whole collection for background lookup (≈1 record/sec with a token)."""
        self._queue = deque(records)
        self._wake.set()

    def pending(self):
        return sum(1 for r in list(self._queue) if self._needs_fetch(r["id"]))

    def _prefetch_loop(self):
        while True:
            self._wake.wait(30)
            self._wake.clear()
            while self._queue:
                record = self._queue.popleft()
                if not self._needs_fetch(record["id"]):
                    continue
                try:
                    self._fetch_once(record)
                except RateLimited:
                    self._queue.appendleft(record)
                    time.sleep(60)
                    continue
                except Exception as e:
                    # Not cached, so it's retried on the next start or when you pick the record.
                    print(f"[metadata] {record['artist']} - {record['title']}: {e}")
                # Discogs allows 60 req/min with a token, 25 without.
                time.sleep(1.2 if self.get_token() else 2.6)
            self.save()
