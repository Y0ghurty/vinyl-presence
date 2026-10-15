"""Listening stats ("Vinyl Wrapped") from the play history.

Each history entry is one listening session of a record: {"id", "at", "secs"}.
Sessions without a measured time ("secs": from before it was tracked, or just
started) use the album's running time, but never longer than until the next
session started, or until now for the one that's playing. Playing the same record again right after itself counts as one
play, however long the break, and anything shorter than a minute (a mis-click)
doesn't count as a play.
"""
import datetime as dt
import time

PERIODS = ["This month", "Last 30 days", "This year", "All time"]
MIN_PLAY_SECONDS = 60


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


def sessions(core):
    """[(entry, seconds)] oldest first. Unmeasured sessions are estimated, but can't outlast the next one's start."""
    hist = core.history  # newest first
    out = []
    for i, h in enumerate(hist):
        secs = h.get("secs")
        if secs is None:
            # Not measured yet (or the app closed before it could save): the album's running time, but never
            # longer than until the next session started, or until now for the one that's playing.
            until = hist[i - 1]["at"] if i > 0 else time.time()
            secs = min(_album_seconds(core, h["id"]), max(0, until - h["at"]))
        out.append((h, secs))
    out.reverse()
    return out


def plays(rows):
    """Group sessions into plays: [(record id, seconds)]. The same record twice in a row is one play; < 1 minute isn't one."""
    grouped = []  # [record id, seconds]
    for h, secs in rows:
        if secs < MIN_PLAY_SECONDS:
            continue
        if grouped and grouped[-1][0] == h["id"]:
            grouped[-1][1] += secs
        else:
            grouped.append([h["id"], secs])
    return [(rid, secs) for rid, secs in grouped]


def compute(core, period):
    since = period_start(period)
    rows = [(h, s) for h, s in sessions(core) if h["at"] >= since and str(h["id"]) in core.records]
    records, artists = {}, {}
    for rid, secs in plays(rows):
        rec = core.records[str(rid)]
        for key, bucket in ((rid, records), (rec["artist"], artists)):
            n, s = bucket.get(key, (0, 0))
            bucket[key] = (n + 1, s + secs)
    top_records = [(core.records[str(rid)], n, secs)
                   for rid, (n, secs) in sorted(records.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:10]]
    top_time = [(core.records[str(rid)], n, secs)
                for rid, (n, secs) in sorted(records.items(), key=lambda kv: (-kv[1][1], -kv[1][0]))[:10]]
    top_artists = [(name, n, secs)
                   for name, (n, secs) in sorted(artists.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:10]]
    return {
        "seconds": sum(s for _h, s in rows),  # every second counts, mis-clicks included
        "sessions": sum(n for n, _s in records.values()),
        "records": len(records),
        "top_records": top_records,  # most played first
        "top_time": top_time,  # most time listened first
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
    for h, secs in sessions(core):
        d = dt.date.fromtimestamp(h["at"])
        if (d.year, d.month) in totals and str(h["id"]) in core.records:
            totals[(d.year, d.month)] += secs / 3600
    return [(dt.date(y, m, 1).strftime("%b"), totals[(y, m)]) for y, m in keys]


# ---------------------------------------------------------------- stylus wear

def stylus_hours(core):
    """Hours on the current stylus: listening since it was fitted, plus what it already had then."""
    since = core.config.get("stylus_since") or 0
    seconds = sum(secs for h, secs in sessions(core) if h["at"] >= since)
    return float(core.config.get("stylus_start_hours") or 0) + seconds / 3600


# ---------------------------------------------------------------- rediscover your shelf

def last_played(core):
    """record id -> when it was last played."""
    last = {}
    for h in core.history:
        last.setdefault(h["id"], h["at"])
    return last


def not_played_lately(core, months=6):
    """Records never played in Vinyl Presence, or not for `months` months. Never played come first."""
    last = last_played(core)
    cutoff = time.time() - months * 30 * 86400
    never = [r for r in core.records.values() if r["id"] not in last]
    old = sorted((r for r in core.records.values() if r["id"] in last and last[r["id"]] < cutoff),
                 key=lambda r: last[r["id"]])
    return never, old


def on_this_day(core):
    """Records you played on this date in earlier years: [(record, year)], most recent year first."""
    today = dt.date.today()
    seen, out = set(), []
    for h in core.history:
        d = dt.date.fromtimestamp(h["at"])
        if (d.month, d.day) == (today.month, today.day) and d.year < today.year and h["id"] not in seen:
            rec = core.records.get(str(h["id"]))
            if rec:
                seen.add(h["id"])
                out.append((rec, d.year))
    return out


def decades(core):
    return sorted({int(r["year"]) // 10 * 10 for r in core.records.values() if r["year"].isdigit()})


def top_genres(core, count=14):
    counts = {}
    for r in core.records.values():
        for g in r.get("genres", []):
            counts[g] = counts.get(g, 0) + 1
    return [g for g, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:count]]
