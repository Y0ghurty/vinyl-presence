"""Music videos for the song that's playing: Discogs' own video links first, then a YouTube search.

Every song is looked up once and remembered in data/videos.json (including the video you picked
yourself), because YouTube's search has a daily limit shared by everyone using the app.
"""
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from metadata import USER_AGENT, _get_json, norm, norm_core

try:  # the app's YouTube Data API key; filled in locally and by the release build
    from youtube_keys import API_KEY as YOUTUBE_KEY
except ImportError:
    YOUTUBE_KEY = ""

YOUTUBE_SEARCH = "https://www.googleapis.com/youtube/v3/search"
VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/embed/|/shorts/)([\w-]{11})")


class VideoError(Exception):
    """A message for the user, plus a YouTube search link to fall back on."""

    def __init__(self, message, search_url=None):
        super().__init__(message)
        self.search_url = search_url


def youtube_available():
    return bool(YOUTUBE_KEY.strip())


def search_url(artist, title):
    return "https://www.youtube.com/results?" + urllib.parse.urlencode({"search_query": f"{artist} {title}"})


def _score(video_title, title, artist):
    """How well a video's title fits the song: 0 = not this song."""
    got, want = norm(video_title), norm_core(title) or norm(title)
    if len(want) < 3 or want not in got:
        return 0
    score = 2
    if artist and norm(artist) and norm(artist) in got:
        score += 1
    if "official" in got:
        score += 1
    if any(w in got for w in ("cover", "karaoke", "reaction", "lesson", "tutorial")):
        score -= 2
    return max(score, 0)


class VideoFinder:
    def __init__(self, cache_path, discogs_token):
        self.path = cache_path
        self.discogs_token = discogs_token  # callable, so a token added later is used
        self.lock = threading.Lock()
        try:
            self.cache = json.loads(cache_path.read_text("utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        self.cache.setdefault("songs", {})
        self.cache.setdefault("releases", {})

    @staticmethod
    def _key(rec, track):
        return f"{rec['id']}|{norm(track['title']) if track else ''}"

    def candidates(self, rec, track):
        """Videos for this song (or the album, without a track), best first: [{'id', 'title', 'source'}]."""
        key = self._key(rec, track)
        with self.lock:
            hit = self.cache["songs"].get(key)
        if hit and hit["candidates"]:
            return hit["candidates"]
        artist = (track or {}).get("artist") or rec["artist"]
        title = track["title"] if track else rec["title"]
        found = []
        if track:  # Discogs links videos to the release, not to songs: match them by title
            ranked = sorted(((s, v) for v in self._discogs_videos(rec["id"])
                             if (s := _score(v["title"], title, artist))), key=lambda x: -x[0])
            found = [{**v, "source": "discogs"} for _, v in ranked]
        if not found:
            found = self._youtube(artist, title, song=bool(track))
        found = list({v["id"]: v for v in found}.values())[:3]  # no doubles, best 3
        if found:
            with self.lock:
                self.cache["songs"][key] = {"candidates": found, "at": int(time.time())}
                self._save()
        return found

    def choose(self, rec, track, video):
        """Remember the video picked by hand: it comes first from now on."""
        key = self._key(rec, track)
        with self.lock:
            entry = self.cache["songs"].setdefault(key, {"candidates": [], "at": int(time.time())})
            entry["candidates"] = [video] + [v for v in entry["candidates"] if v["id"] != video["id"]]
            self._save()

    def _discogs_videos(self, release_id):
        rid = str(release_id)
        with self.lock:
            if rid in self.cache["releases"]:
                return self.cache["releases"][rid]
        token = self.discogs_token()
        headers = {"Authorization": f"Discogs token={token}"} if token else {}
        try:
            data = _get_json(f"https://api.discogs.com/releases/{rid}", headers)
        except (urllib.error.URLError, ValueError):
            return []
        videos = []
        for v in data.get("videos") or []:
            m = VIDEO_ID.search(v.get("uri") or "")
            if m:
                videos.append({"id": m.group(1), "title": v.get("title") or ""})
        with self.lock:
            self.cache["releases"][rid] = videos
            self._save()
        return videos

    def _youtube(self, artist, title, song=True):
        if not youtube_available():
            return []
        query = f"{artist} {title} official video" if song else f"{artist} {title}"
        params = {"part": "snippet", "type": "video", "maxResults": 6, "videoEmbeddable": "true",
                  "q": query, "key": YOUTUBE_KEY.strip()}
        req = urllib.request.Request(f"{YOUTUBE_SEARCH}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.load(resp)
        except urllib.error.HTTPError as e:
            try:
                reason = json.loads(e.read() or b"{}")["error"]["errors"][0]["reason"]
            except (ValueError, KeyError, IndexError):
                reason = ""
            if reason in ("quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"):
                raise VideoError("YouTube's daily search limit is used up. It resets tomorrow; "
                                 "until then you can watch it on YouTube.", search_url(artist, title)) from e
            raise VideoError(f"YouTube search didn't work ({e.code}).", search_url(artist, title)) from e
        except urllib.error.URLError as e:
            raise VideoError("Couldn't reach YouTube. Are you online?", search_url(artist, title)) from e
        results = []
        for item in data.get("items") or []:
            vid = (item.get("id") or {}).get("videoId")
            snippet = item.get("snippet") or {}
            if vid:
                video_title = html.unescape(snippet.get("title") or "")  # titles come with &amp; etc.
                channel = snippet.get("channelTitle") or ""
                score = _score(video_title, title, artist) if song else 1
                if norm(artist) and norm(artist) in norm(channel):
                    score += 1  # the artist's own channel (or their "- Topic" / VEVO channel)
                results.append((score, {"id": vid, "title": video_title, "source": "youtube"}))
        good = [v for s, v in sorted(results, key=lambda x: -x[0]) if s > 0]
        return good or [v for _, v in results][:1]

    def _save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache, ensure_ascii=False), "utf-8")
        tmp.replace(self.path)
