"""Stats computation from session history."""
import time

import conftest
from conftest import FakeCore

import stats as stats_mod


def test_period_start_all_time():
    assert stats_mod.period_start("All time") == 0


def test_compute_aggroups_and_ranks():
    # one record, two sessions
    rec = conftest.record(1, "Pink Floyd", "The Wall")
    c = FakeCore(records=[rec])
    c.records["1"] = rec
    c.history = [
        {"id": 1, "at": 100, "secs": 2000},
        {"id": 1, "at": 200, "secs": 1000},
    ]
    res = stats_mod.compute(c, "All time")
    assert res["sessions"] == 2
    assert res["records"] == 1
    assert res["seconds"] == 3000
    # top_records: (rec, plays, secs)
    assert res["top_records"][0][1] == 2
    assert res["top_records"][0][2] == 3000
    assert [a[0] for a in res["top_artists"]] == ["Pink Floyd"]


def test_compute_uses_album_seconds_when_secs_missing():
    rec = conftest.record(1)
    c = FakeCore(records=[rec])
    c.records["1"] = rec
    c.history = [{"id": 1, "at": 100}]  # no secs
    # album running time from the (empty) cached info -> 0 fallback, still counts a session
    res = stats_mod.compute(c, "All time")
    assert res["sessions"] == 1


def test_compute_filters_by_period():
    rec = conftest.record(1)
    c = FakeCore(records=[rec])
    c.records["1"] = rec
    now = time.time()
    c.history = [
        {"id": 1, "at": now - 100, "secs": 100},     # within last 30 days
        {"id": 1, "at": now - 40 * 86400, "secs": 100},  # older than 30 days
    ]
    res = stats_mod.compute(c, "Last 30 days")
    assert res["sessions"] == 1


def test_compute_ignores_stranger_ids_and_top_reorder():
    rec = conftest.record(1, artist="A", title="One")
    rec2 = conftest.record(2, artist="B", title="Two")
    c = FakeCore(records=[rec, rec2])
    c.records["1"] = rec
    c.records["2"] = rec2
    c.history = [
        {"id": 1, "at": 100, "secs": 50},
        {"id": 2, "at": 101, "secs": 20},
        {"id": 1, "at": 102, "secs": 50},
        {"id": 999, "at": 103, "secs": 999},  # not in collection -> skipped
    ]
    res = stats_mod.compute(c, "All time")
    assert res["sessions"] == 3
    assert [r[1] for r in res["top_records"]] == [2, 1]  # most plays first


def test_months_bucketizes_hours():
    rec = conftest.record(1)
    c = FakeCore(records=[rec])
    c.records["1"] = rec
    import datetime as dt
    now = dt.datetime.now()
    this_month = dt.datetime(now.year, now.month, 5).timestamp()
    c.history = [{"id": 1, "at": int(this_month), "secs": 3600}]  # 1 hour
    labels = stats_mod.months(c, count=3)
    assert len(labels) == 3
    # last entry is the current month with 1.0 hours
    assert labels[-1][0] == dt.date.today().strftime("%b")
    assert abs(labels[-1][1] - 1.0) < 1e-6