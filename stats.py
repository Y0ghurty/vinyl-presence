"""Listening stats ("Vinyl Wrapped") from the play history.

Each history entry is one listening session of a record: {"id", "at", "secs"}.
Sessions from before listening time was tracked have no "secs"; for those the
album's running time is used when it's known.
"""
import datetime as dt
import time

PERIODS = ["This month", "Last 30 days", "This year", "All time"]


def period_start(name):
    today = dt.date.today()
    if name == "This month":
        return time.mktime(today.replace(day=1).timetuple())
    if name == "Last 30 days":
        return time.time() - 30 * 86400
    if name == "This year":
        return time.mktime(today.replace(month=1, day=1).timetuple())
    return 0


def _album_seconds(core, rid):
    info = core.library.get_cached(rid) or {}
    return sum(t["duration"] or 0 for t in info.get("tracks") or [])


def _session_seconds(core, entry):
    return entry["secs"] if entry.get("secs") is not None else _album_seconds(core, entry["id"])


def compute(core, period):
    since = period_start(period)
    sessions = [h for h in core.history if h["at"] >= since and str(h["id"]) in core.records]
    records, artists, total = {}, {}, 0
    for h in sessions:
        rec = core.records[str(h["id"])]
        secs = _session_seconds(core, h)
        total += secs
        for key, bucket in ((h["id"], records), (rec["artist"], artists)):
            plays, s = bucket.get(key, (0, 0))
            bucket[key] = (plays + 1, s + secs)
    top_records = [(core.records[str(rid)], plays, secs)
                   for rid, (plays, secs) in sorted(records.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:10]]
    top_artists = [(name, plays, secs)
                   for name, (plays, secs) in sorted(artists.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:10]]
    return {
        "seconds": total,
        "sessions": len(sessions),
        "records": len(records),
        "top_records": top_records,
        "top_artists": top_artists,
    }


def months(core, count=12):
    """Hours listened per month for the last `count` months, oldest first: [(label, hours), ...]."""
    today = dt.date.today()
    keys = []
    y, m = today.year, today.month
    for _ in range(count):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    totals = {k: 0.0 for k in keys}
    for h in core.history:
        d = dt.date.fromtimestamp(h["at"])
        if (d.year, d.month) in totals and str(h["id"]) in core.records:
            totals[(d.year, d.month)] += _session_seconds(core, h) / 3600
    return [(dt.date(y, m, 1).strftime("%b"), totals[(y, m)]) for y, m in keys]
