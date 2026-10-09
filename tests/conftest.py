"""Shared fixtures for offline testing.

The app talks to Discord, Discogs and Deezer and drives a tkinter UI; none of
that is needed to exercise the pure logic. These fixtures stand in for the
Core/Player plumbing with lightweight doubles so the state machine, parsing,
search and payload-builder code can be tested deterministically.
"""
import threading

import pytest

import core as core_mod


class FakeClock:
    """Controllable now_ms(). Player._ticker threads are patched out in tests,
    so the only time that flows is what a test advances explicitly."""

    def __init__(self, start_ms=0):
        self.now = start_ms

    def __call__(self):
        return self.now

    def advance(self, ms):
        self.now += ms


class FakeLibrary:
    def __init__(self, info=None):
        self.info = info or {}

    def get(self, record, wait=True):
        return {"tracks": self.info.get("tracks", []), "cover": self.info.get("cover"),
                "thumb": self.info.get("thumb"), "artist_id": self.info.get("artist_id")}

    def get_cached(self, rid):
        return self.get({"id": rid})


class FakeDiscord:
    def __init__(self):
        self.activity = None
        self.calls = []

    def set_activity(self, activity):
        self.calls.append(activity)
        self.activity = activity


class FakeCore:
    """The slice of Core that Player uses, plus what stats needs."""

    def __init__(self, records=None, config=None, library=None, history=None):
        self.records = {str(r["id"]): r for r in (records or [])}
        self.config = {**core_mod.DEFAULT_CONFIG, **(config or {})}
        self.library = library or FakeLibrary()
        self.history = list(history or [])
        self.discord = FakeDiscord()
        self.shared = {}

    def add_history(self, rid):
        self.history = [{"id": int(rid), "at": int(__import__("time").time())}] + self.history[:19999]

    def set_listened(self, rid, secs):
        if self.history and self.history[0]["id"] == int(rid):
            self.history[0]["secs"] = int(secs)

    def share_now_spinning(self, rec, info):
        self.shared[rec["id"]] = True

    def has_badge(self):
        return False


def record(rid=1, artist="Pink Floyd", title="The Dark Side Of The Moon", format="Vinyl, LP, Album",
           label="Harvest", catno="SHVL 804", year="1973", added="2020-01-01"):
    r = {"id": rid, "artist": artist, "title": title, "label": label, "catno": catno,
         "format": format, "year": year, "added": added}
    return core_mod.index_record(r)


TRACKS = [
    {"pos": "A1", "title": "Speak To Me", "artist": "", "duration": 77, "side": "A"},
    {"pos": "A2", "title": "Breathe", "artist": "", "duration": 170, "side": "A"},
    {"pos": "B1", "title": "Us And Them", "artist": "", "duration": 469, "side": "B"},
]


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def fake_core():
    return FakeCore()


@pytest.fixture
def no_ticker_threads(monkeypatch):
    """Player starts a daemon _ticker thread that mutates state on wall-clock time;
    neutralise it so tests drive _tick() explicitly via the fake clock."""
    def fake_start(self, *a, **k):
        return None  # don't spawn the background ticker
    monkeypatch.setattr(threading.Thread, "start", fake_start)


@pytest.fixture
def make_player(monkeypatch, no_ticker_threads):
    def _make(records=None, config=None, library=None, history=None, clock=None):
        core = FakeCore(records=records, config=config, library=library, history=history)
        if clock is not None:
            monkeypatch.setattr(core_mod, "now_ms", clock)
        p = core_mod.Player(core)
        p.now = None
        return p, core
    return _make