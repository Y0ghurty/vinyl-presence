"""Achievements, worked out from your own play history and collection.

They're shown on your member page on vinylhangout.com. Each one has the moment you earned it,
found by replaying your history from the start.
"""
import datetime as dt
import time

import stats

# id: (icon, name, what it takes)
CATALOG = {
    "plays-1": ("🎶", "First drop", "Played your first record"),
    "plays-10": ("💿", "Warming up", "Played 10 records"),
    "plays-50": ("🎧", "Regular", "Played 50 records"),
    "plays-100": ("💯", "Century", "Played 100 records"),
    "plays-250": ("🔊", "Heavy rotation", "Played 250 records"),
    "plays-500": ("🌀", "Wax addict", "Played 500 records"),
    "plays-1000": ("👑", "Vinyl legend", "Played 1,000 records"),
    "hours-10": ("⏱️", "10 hours", "Listened to 10 hours of vinyl"),
    "hours-50": ("⌛", "50 hours", "Listened to 50 hours of vinyl"),
    "hours-100": ("🕯️", "100 hours", "Listened to 100 hours of vinyl"),
    "hours-250": ("🌙", "250 hours", "Listened to 250 hours of vinyl"),
    "hours-500": ("🌌", "500 hours", "Listened to 500 hours of vinyl"),
    "records-25": ("🗂️", "Crate digger", "Played 25 different records"),
    "records-100": ("🔎", "Deep cuts", "Played 100 different records"),
    "records-250": ("🧭", "Shelf explorer", "Played 250 different records"),
    "collection-100": ("📚", "Hundred club", "Has 100 records in the collection"),
    "collection-250": ("🏛️", "Serious collector", "Has 250 records in the collection"),
    "collection-500": ("🏰", "Record room", "Has 500 records in the collection"),
    "collection-1000": ("🧱", "Wall of sound", "Has 1,000 records in the collection"),
    "night-owl": ("🦉", "Night owl", "Put a record on between midnight and 5 am"),
    "early-bird": ("🌅", "Early bird", "Put a record on between 5 and 8 am"),
    "marathon": ("🏃", "Marathon", "Listened to 5 hours of vinyl in one day"),
    "streak-7": ("🔥", "On a roll", "Played vinyl 7 days in a row"),
    "seven-inch": ("🎵", "Seven inch", "Played a 7-inch single"),
    "box-set": ("📦", "Box set", "Played a box set"),
    "time-traveller": ("🕰️", "Time traveller", "Played records from 5 different decades"),
    "rediscovery": ("🔁", "Rediscovered", "Played a record again after more than 6 months"),
    "completist": ("🏆", "Completist", "Played every record you own by one artist (at least 3)"),
}

PLAYS = (1, 10, 50, 100, 250, 500, 1000)
HOURS = (10, 50, 100, 250, 500)
DIFFERENT = (25, 100, 250)
COLLECTION = (100, 250, 500, 1000)


def _added(rec):
    """When a record was added to the collection, as a Unix time (None if unknown)."""
    try:
        return int(dt.datetime.fromisoformat((rec.get("added") or "")[:19]).timestamp())
    except ValueError:
        return None


def _replay(core):
    """Replays the history: ({achievement id: when it was earned}, {"plays", "hours", "records", "collection"})."""
    got = {}

    def earn(key, at):
        got.setdefault(key, int(at))

    count = seconds = streak = 0
    seen, decades, last_played, day_secs = set(), set(), {}, {}
    last_day = None
    owned_by = {}  # artist -> their record ids in the collection
    for rec in core.records.values():
        owned_by.setdefault(rec["artist"], set()).add(str(rec["id"]))
    played_by = {}

    for rid, at, secs in stats.timed_plays(stats.sessions(core)):
        rec = core.records.get(str(rid))
        count += 1
        for n in PLAYS:
            if count == n:
                earn(f"plays-{n}", at)
        before, seconds = seconds, seconds + secs
        for h in HOURS:
            if before < h * 3600 <= seconds:
                earn(f"hours-{h}", at + secs)
        seen.add(rid)
        for n in DIFFERENT:
            if len(seen) == n:
                earn(f"records-{n}", at)

        local = time.localtime(at)
        if local.tm_hour < 5:
            earn("night-owl", at)
        elif local.tm_hour < 8:
            earn("early-bird", at)
        day = dt.date(local.tm_year, local.tm_mon, local.tm_mday)
        day_secs[day] = day_secs.get(day, 0) + secs
        if day_secs[day] >= 5 * 3600:
            earn("marathon", at + secs)
        if day != last_day:
            streak = streak + 1 if last_day and (day - last_day).days == 1 else 1
            last_day = day
            if streak >= 7:
                earn("streak-7", at)

        if rid in last_played and at - last_played[rid] > 182 * 86400:
            earn("rediscovery", at)
        last_played[rid] = at

        if rec:
            fmt = rec.get("format") or ""
            if '7"' in fmt:
                earn("seven-inch", at)
            if "box set" in fmt.lower():
                earn("box-set", at)
            year = str(rec.get("year") or "")
            if year[:4].isdigit() and int(year[:4]) > 1900:
                decades.add(int(year[:4]) // 10)
                if len(decades) >= 5:
                    earn("time-traveller", at)
            mine = owned_by.get(rec["artist"], set())
            played = played_by.setdefault(rec["artist"], set())
            played.add(str(rid))
            if len(mine) >= 3 and mine <= played:
                earn("completist", at)

    dates = sorted(d for d in (_added(r) for r in core.records.values()) if d)
    total = len(core.records)
    for n in COLLECTION:
        if total >= n:
            earn(f"collection-{n}", dates[n - 1] if len(dates) >= n else time.time())
    return got, {"plays": count, "hours": seconds / 3600, "records": len(seen), "collection": total}


def earned(core):
    """{achievement id: when it was earned}, from the history and the collection."""
    return _replay(core)[0]


def progress(core):
    """Every achievement in catalog order: {"id", "icon", "name", "desc", "at" (None if not yet),
    "have", "goal"} (have/goal only for the ones that count something)."""
    got, totals = _replay(core)
    out = []
    for key, (icon, name, desc) in CATALOG.items():
        kind, _, n = key.rpartition("-")
        counted = kind in totals and n.isdigit()
        out.append({"id": key, "icon": icon, "name": name, "desc": desc, "at": got.get(key),
                    "have": totals[kind] if counted else None, "goal": int(n) if counted else None})
    return out


def for_page(core):
    """The earned achievements, newest first, ready for the member page."""
    got = earned(core)
    return [{"id": key, "icon": CATALOG[key][0], "name": CATALOG[key][1], "desc": CATALOG[key][2], "at": at}
            for key, at in sorted(got.items(), key=lambda kv: -kv[1])]
