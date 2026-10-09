"""Tiny dependency-free Discord IPC client for Rich Presence.

Talks to the Discord desktop app over its local pipe/socket, the same way
games and the Spotify integration do. A bot account can't set *your* status,
so this is the piece that actually puts the record on your profile.
"""
import json
import os
import socket
import struct
import sys
import threading
import uuid

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = range(5)


class DiscordError(Exception):
    pass


class InvalidClientId(DiscordError):
    pass


def _open_connection():
    if sys.platform == "win32":
        for i in range(10):
            try:
                return open(rf"\\?\pipe\discord-ipc-{i}", "r+b", buffering=0)
            except OSError:
                continue
    else:
        bases = [os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp"]
        for base in filter(None, bases):
            for sub in ("", "app/com.discordapp.Discord", "snap.discord", ".flatpak/com.discordapp.Discord/xdg-run"):
                for i in range(10):
                    path = os.path.join(base, sub, f"discord-ipc-{i}")
                    if not os.path.exists(path):
                        continue
                    try:
                        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                        s.connect(path)
                        return s.makefile("rwb", buffering=0)
                    except OSError:
                        continue
    raise DiscordError("Discord app not found. Is Discord running on this computer?")


class DiscordIPC:
    def __init__(self, client_id):
        self.client_id = str(client_id)
        self._conn = None
        self.user = None

    def connect(self):
        self._conn = _open_connection()
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        op, data = self._recv()
        if op == OP_CLOSE:
            self.close()
            if data.get("code") == 4000:
                raise InvalidClientId(data.get("message") or "Invalid Application ID")
            raise DiscordError(data.get("message") or "Discord closed the connection")
        self.user = (data.get("data") or {}).get("user") or {}
        return self.user

    def close(self):
        if self._conn:
            try:
                self._send(OP_CLOSE, {})
            except OSError:
                pass
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn = None

    def set_activity(self, activity):
        """Set (or clear, with None) the presence. Raises DiscordError on rejection."""
        args = {"pid": os.getpid()}
        if activity:
            args["activity"] = activity
        nonce = str(uuid.uuid4())
        self._send(OP_FRAME, {"cmd": "SET_ACTIVITY", "args": args, "nonce": nonce})
        while True:
            op, data = self._recv()
            if op == OP_PING:
                self._send(OP_PONG, data)
                continue
            if op == OP_CLOSE:
                self.close()
                raise ConnectionError(data.get("message") or "Discord closed the connection")
            if data.get("nonce") != nonce:
                continue
            if data.get("evt") == "ERROR":
                raise DiscordError((data.get("data") or {}).get("message") or "SET_ACTIVITY failed")
            return data

    def _send(self, op, payload):
        body = json.dumps(payload).encode("utf-8")
        self._conn.write(struct.pack("<II", op, len(body)) + body)

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self._conn.read(n - len(buf))
            if not chunk:
                raise ConnectionError("Discord connection lost")
            buf += chunk
        return buf

    def _recv(self):
        op, length = struct.unpack("<II", self._read_exact(8))
        return op, json.loads(self._read_exact(length).decode("utf-8") or "{}")


# Fields Discord added in 2025. If an older client rejects them we retry without.
_NEWER_FIELDS = ("name", "status_display_type", "details_url", "state_url")
_NEWER_ASSET_FIELDS = ("large_url", "small_url")


def _downgrade(activity, level):
    if not activity or level == 0:
        return activity
    a = dict(activity)
    for k in _NEWER_FIELDS:
        a.pop(k, None)
    if "assets" in a:
        a["assets"] = {k: v for k, v in a["assets"].items() if k not in _NEWER_ASSET_FIELDS}
    if level >= 2:
        a.pop("type", None)
        a.pop("buttons", None)
    return a


class PresenceWorker(threading.Thread):
    """Owns the Discord connection in the background, so the web UI never blocks.

    Call set_activity() whenever the desired presence changes; the worker
    (re)connects as needed and re-applies it if Discord restarts.
    """

    HEARTBEAT = 20

    def __init__(self):
        super().__init__(daemon=True, name="discord-presence")
        self._cond = threading.Condition()
        self._activity = None
        self._client_id = ""
        self._dirty = True
        self._reset = False
        self._stopping = False
        self._ipc = None
        self._compat = 0
        self.status = {"state": "starting", "user": None, "error": None}

    def set_client_id(self, client_id):
        with self._cond:
            client_id = (client_id or "").strip()
            if client_id != self._client_id:
                self._client_id = client_id
                self._reset = True
                self._dirty = True
                self._cond.notify()

    def set_activity(self, activity):
        with self._cond:
            self._activity = activity
            self._dirty = True
            self._cond.notify()

    def run(self):
        while True:
            with self._cond:
                self._cond.wait_for(lambda: self._dirty, timeout=self.HEARTBEAT)
                activity, client_id, reset = self._activity, self._client_id, self._reset
                self._dirty = self._reset = False
            if self._stopping:
                if self._ipc:
                    try:
                        self._ipc.set_activity(None)
                    except (OSError, DiscordError):
                        pass
                self._disconnect()
                return
            if reset:
                self._disconnect()
                self._compat = 0
            if not client_id:
                self.status = {"state": "no_client_id", "user": None, "error": None}
                continue
            if self.status["state"] == "bad_client_id" and not reset:
                continue
            try:
                if not self._ipc:
                    self.status = {"state": "connecting", "user": None, "error": None}
                    ipc = DiscordIPC(client_id)
                    user = ipc.connect()
                    self._ipc = ipc
                    name = user.get("global_name") or user.get("username")
                    self.status = {"state": "connected", "user": name, "error": None}
                    print(f"[discord] connected as {name}")
                self._send(activity)
            except InvalidClientId as e:
                self._disconnect()
                self.status = {"state": "bad_client_id", "user": None, "error": str(e)}
                print(f"[discord] {e}")
            except (OSError, DiscordError) as e:
                self._disconnect()
                self.status = {"state": "not_running", "user": None, "error": str(e)}

    def _send(self, activity):
        while True:
            try:
                resp = self._ipc.set_activity(_downgrade(activity, self._compat))
                self.status["error"] = None
                self._check_name(activity, resp)
                return
            except ConnectionError:
                raise
            except DiscordError as e:
                if activity and self._compat < 2:
                    self._compat += 1
                    print(f"[discord] rejected presence ({e}); retrying in compatibility mode {self._compat}")
                    continue
                self.status["error"] = str(e)
                print(f"[discord] {e}")
                return

    def _check_name(self, activity, resp):
        """Discord echoes the activity it applied, so we can tell if it kept our card title."""
        wanted = (activity or {}).get("name")
        if not wanted:
            return
        got = (resp.get("data") or {}).get("name") if self._compat == 0 else None
        ignored = got != wanted
        if ignored != self.status.get("name_ignored"):
            print(f"[discord] card title {'ignored, Discord shows the app name' if ignored else 'accepted'}"
                  f" (sent {wanted!r}, Discord shows {got!r})")
        self.status["name_ignored"] = ignored

    def _disconnect(self):
        if self._ipc:
            self._ipc.close()
        self._ipc = None

    def shutdown(self, timeout=2):
        """Clear the status and close the connection (Discord also clears it if we just exit)."""
        with self._cond:
            self._stopping = True
            self._dirty = True
            self._cond.notify()
        self.join(timeout)
