"""Scrobbling to Last.fm: "Scrobbling now" on your profile, and tracks counted once they've played.

Connecting uses Last.fm's desktop sign-in: the app gets a token, you approve it on
last.fm in your browser, and the app receives a session key (it never sees your
password). Scrobbles are queued in data/scrobbles.json and sent in the background,
so listening offline still counts (Last.fm accepts them up to two weeks back).
"""
import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from metadata import USER_AGENT

try:  # the app's own Last.fm API account; filled in locally and by the release build
    from lastfm_keys import API_KEY, SHARED_SECRET
except ImportError:
    API_KEY = SHARED_SECRET = ""

API = "https://ws.audioscrobbler.com/2.0/"
MIN_TRACK_SECONDS = 30  # Last.fm ignores shorter tracks
MAX_WAIT_SECONDS = 240  # ...and counts a track after half its length or 4 minutes
MAX_AGE_SECONDS = 14 * 86400 - 3600  # Last.fm refuses scrobbles older than two weeks
RETRY_SECONDS = 120
ERR_BAD_SESSION, ERR_NOT_AUTHORIZED, ERR_TOKEN_EXPIRED = 9, 14, 15


class LastFMError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def available():
    return bool(API_KEY.strip() and SHARED_SECRET.strip())


def _call(method, params=None, post=False):
    p = {**(params or {}), "method": method, "api_key": API_KEY.strip()}
    p = {k: str(v) for k, v in p.items() if v not in (None, "")}
    p["api_sig"] = hashlib.md5(("".join(k + p[k] for k in sorted(p)) + SHARED_SECRET.strip()).encode("utf-8")).hexdigest()
    p["format"] = "json"
    body = urllib.parse.urlencode(p).encode("utf-8")
    req = (urllib.request.Request(API, data=body, method="POST", headers={"User-Agent": USER_AGENT}) if post
           else urllib.request.Request(f"{API}?{body.decode()}", headers={"User-Agent": USER_AGENT}))
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as e:  # Last.fm puts the real error in the body
        try:
            data = json.loads(e.read() or b"{}")
        except ValueError:
            raise LastFMError(0, f"Last.fm answered with HTTP {e.code}") from e
    if "error" in data:
        raise LastFMError(data["error"], data.get("message") or "Last.fm error")
    return data


class LastFM:
    def __init__(self, core, queue_path):
        self.core = core
        self.queue_path = queue_path
        self.lock = threading.Lock()
        try:
            self.queue = json.loads(queue_path.read_text("utf-8"))
        except (OSError, ValueError):
            self.queue = []
        self.status = {"state": "idle"}  # for Settings: idle / waiting / error
        self.last_error = None
        self._wake = threading.Event()
        threading.Thread(target=self._sender, daemon=True, name="lastfm").start()

    # -- state

    def connected(self):
        return available() and bool(self.core.config.get("lastfm_session"))

    def active(self):
        return self.connected() and self.core.config.get("lastfm_enabled", True)

    def pending(self):
        with self.lock:
            return len(self.queue)

    # -- connecting (desktop sign-in)

    def start_connect(self):
        """Returns (token, url to open in the browser)."""
        token = _call("auth.getToken")["token"]
        return token, f"https://www.last.fm/api/auth/?api_key={API_KEY.strip()}&token={token}"

    def finish_connect(self, token, timeout=300):
        """Wait until the user has approved in the browser; returns the Last.fm username."""
        self.status = {"state": "waiting"}
        deadline = time.time() + timeout
        try:
            while time.time() < deadline:
                try:
                    session = _call("auth.getSession", {"token": token})["session"]
                except LastFMError as e:
                    if e.code == ERR_NOT_AUTHORIZED:
                        time.sleep(3)  # not approved yet
                        continue
                    raise
                self.core.update_config({"lastfm_session": session["key"], "lastfm_user": session["name"],
                                         "lastfm_enabled": True})
                self._wake.set()  # send anything that was waiting
                return session["name"]
            raise LastFMError(ERR_TOKEN_EXPIRED, "Took too long. Click Connect again.")
        finally:
            self.status = {"state": "idle"}

    def disconnect(self):
        self.core.update_config({"lastfm_session": "", "lastfm_user": ""})

    # -- what the player reports

    def now_playing(self, rec, track):
        if not self.active():
            return
        params = self._track_params(rec, track)
        threading.Thread(target=self._quietly, args=("track.updateNowPlaying", params), daemon=True,
                         name="lastfm-now").start()

    def track_ended(self, rec, track, started_ms, ended_ms, manual=False):
        """Queue a scrobble if the track played long enough by Last.fm's rules."""
        if not self.active():
            return
        played = (ended_ms - started_ms) / 1000
        duration = track.get("duration")
        if duration:
            if duration <= MIN_TRACK_SECONDS or played < min(duration / 2, MAX_WAIT_SECONDS):
                return
        elif not manual or played < MIN_TRACK_SECONDS:
            return  # without a length we only count tracks you picked yourself
        entry = {**self._track_params(rec, track), "timestamp": int(started_ms / 1000)}
        with self.lock:
            self.queue.append(entry)
            self._save()
        print(f"[lastfm] queued {entry['artist']} - {entry['track']}")
        self._wake.set()

    @staticmethod
    def _track_params(rec, track):
        p = {"artist": track.get("artist") or rec["artist"], "track": track["title"], "album": rec["title"],
             "albumArtist": rec["artist"], "duration": track.get("duration") or None}
        return {k: v for k, v in p.items() if v}

    # -- sending

    def _quietly(self, method, params):
        try:
            _call(method, {**params, "sk": self.core.config["lastfm_session"]}, post=True)
        except (LastFMError, OSError) as e:
            self._handle_error(e)

    def _sender(self):
        while True:
            self._wake.wait(RETRY_SECONDS)
            self._wake.clear()
            if not self.connected():
                continue
            while True:
                with self.lock:
                    cutoff = time.time() - MAX_AGE_SECONDS
                    self.queue = [s for s in self.queue if s["timestamp"] > cutoff]
                    batch = self.queue[:50]
                if not batch:
                    break
                params = {"sk": self.core.config["lastfm_session"]}
                for i, s in enumerate(batch):
                    params.update({f"{k}[{i}]": v for k, v in s.items()})
                try:
                    _call("track.scrobble", params, post=True)
                except (LastFMError, OSError) as e:
                    self._handle_error(e)
                    break  # try again later
                with self.lock:
                    self.queue = self.queue[len(batch):]
                    self._save()
                self.last_error = None
                print(f"[lastfm] scrobbled {len(batch)} track(s)")

    def _handle_error(self, e):
        if isinstance(e, LastFMError) and e.code == ERR_BAD_SESSION:
            self.last_error = "Last.fm signed this app out. Connect again in Settings → Last.fm."
            self.core.update_config({"lastfm_session": ""})
        else:
            self.last_error = f"Couldn't reach Last.fm, will retry: {e}"
        print(f"[lastfm] {e}")

    def _save(self):
        tmp = self.queue_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.queue, ensure_ascii=False), "utf-8")
        tmp.replace(self.queue_path)
