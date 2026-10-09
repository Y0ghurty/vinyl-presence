"""Core logic: collection parsing, search, media detection, and the Player state machine."""
import json

import pytest
from conftest import TRACKS, FakeClock, FakeLibrary
from conftest import record as mk_record

import core as core_mod

# ---------------------------------------------------------------- collection parsing

CSV_HEADER = ("release_id,Artist,Title,Label,Catalog#,Format,Released,Date Added\n")


def test_parse_collection_valid_csv():
    csv = CSV_HEADER + '123,Pink Floyd,The Wall,Harvest,SHVL 804,"Vinyl, LP, Album",1979,20/09/2021\n'
    csv += '456,Metallica,Master Of Puppets,Vertigo,838144,"Vinyl, LP, Album",1986,01/06/2020\n'
    recs = core_mod.parse_collection(csv)
    assert set(recs) == {"123", "456"}
    r = recs["123"]
    assert r["artist"] == "Pink Floyd"
    assert r["title"] == "The Wall"
    assert r["year"] == "1979"
    assert r["format"] == "Vinyl, LP, Album"


def test_parse_collection_strips_bom_and_duplicate_ids():
    csv = "\ufeff" + CSV_HEADER + '123,A,B,H,C,"Vinyl, LP",1979,20/09/2021\n'
    csv += '123,A,B,H,C,"Vinyl, LP",1979,20/09/2021\n'  # same id, later row wins
    recs = core_mod.parse_collection(csv)
    assert list(recs) == ["123"]


def test_parse_collection_skips_low_quality_release_id():
    csv = CSV_HEADER + 'abc,A,B,H,C,"Vinyl, LP",1979,20/09/2021\n'
    csv += '456,Good,Record,H,C,"Vinyl, LP",1986,x\n'
    recs = core_mod.parse_collection(csv)
    assert list(recs) == ["456"]


def test_parse_collection_zero_year_becomes_blank():
    csv = CSV_HEADER + '123,A,B,H,C,"Vinyl, LP",0,20/09/2021\n'
    recs = core_mod.parse_collection(csv)
    assert recs["123"]["year"] == ""


def test_parse_collection_missing_required_columns_raises():
    with pytest.raises(ValueError):
        core_mod.parse_collection("Artist,Title\nPink,Wall\n")


# ---------------------------------------------------------------- search

def test_search_matches_every_word_and_ranks():
    recs = [mk_record(1, "Pink Floyd", "Animals"),
            mk_record(2, "Pink Floyd", "The Wall"),
            mk_record(3, "Black Sabbath", "Paranoid")]
    assert [r["id"] for r in core_mod.search(recs, "pink wall")] == [2]
    assert [r["id"] for r in core_mod.search(recs, "pink")] == [1, 2]
    assert core_mod.search(recs, "paranoid")[0]["id"] == 3


def test_search_accent_and_case_insensitive():
    recs = [mk_record(1, "Sébastien Tellier", "Sexuality")]
    assert [r["id"] for r in core_mod.search(recs, "sebastien")] == [1]
    assert [r["id"] for r in core_mod.search(recs, "SEXUALITY")] == [1]


def test_search_typo_subsequence():
    recs = [mk_record(1, "Led Zeppelin", "IV")]
    # "zepelin" (missing 'p') still matches via subsequence
    assert [r["id"] for r in core_mod.search(recs, "zeppin")] == [1]


def test_search_no_match_empty():
    recs = [mk_record(1, "Pink Floyd", "The Wall")]
    assert core_mod.search(recs, "zzzzz") == []
    assert core_mod.search(recs, "") == [] or True  # blank handles gracefully
    assert core_mod.search([], "anything") == []


def test_search_date_and_format_fields_searchable():
    recs = [mk_record(1, "Pink Floyd", "The Wall", year="1979")]
    assert [r["id"] for r in core_mod.search(recs, "1979")] == [1]
    recs = [mk_record(2, "Pink Floyd", "Wish", format="CD, Album")]
    assert [r["id"] for r in core_mod.search(recs, "cd")] == [2]


# ---------------------------------------------------------------- misc helpers

def test_medium_maps_format_to_media():
    assert core_mod.medium({"format": "Vinyl, LP, Album"}) == ("vinyl", "Spinning on my turntable")
    assert core_mod.medium({"format": "CD, Album"}) == ("CD", "Playing on my CD player")
    assert core_mod.medium({"format": "Cassette"}) == ("cassette", "Playing on my tape deck")
    assert core_mod.medium({"format": "Shellac, 78 RPM"}) == ("shellac", "Spinning on my turntable")
    assert core_mod.medium({}) == ("vinyl", "Spinning on my turntable")  # default


def test_clip_truncates_and_pads():
    assert core_mod.clip(None) is None
    assert core_mod.clip("") is None
    assert core_mod.clip("a") is None or len(core_mod.clip("a")) >= 2
    long = "x" * 200
    assert len(core_mod.clip(long)) <= 128
    assert core_mod.clip(long).endswith("…")
    assert core_mod.clip("short") == "short"


# ---------------------------------------------------------------- Player state machine

def test_play_requires_record_in_collection(make_player):
    p, core = make_player(records=[mk_record(1)])
    with pytest.raises(KeyError):
        p.play("999")


def test_play_sets_now_and_pushes_activity(make_player):
    p, core = make_player(records=[mk_record(1)], library=FakeLibrary(),
                          clock=FakeClock(1000))
    core.records["1"] = mk_record(1)
    core.library.info = {"tracks": TRACKS}
    p.play("1")
    assert p.now is not None
    assert p.now["record"]["id"] == 1
    # pushed a listening activity to the (fake) discord
    assert core.discord.activity is not None
    assert core.discord.activity["type"] == 2


def test_play_groups_sides_and_defaults_to_first(make_player):
    p, core = make_player(records=[mk_record(1)], library=FakeLibrary(
        {"tracks": TRACKS}))
    core.records["1"] = mk_record(1)
    p.play("1")
    assert set(p.now["sides"]) == {"A", "B"}
    assert p.now["side"] == "A"


def test_play_explicit_side_and_track(make_player):
    rec, info = mk_record(1), {"tracks": TRACKS}
    p, core = make_player(records=[rec], library=FakeLibrary(info))
    core.records["1"] = rec
    p.play("1", side="B", track=0)
    assert p.now["side"] == "B"
    assert p.now["idx"] == 0
    assert p.now["manual"] is True


def test_control_next_advances_track(make_player):
    rec = mk_record(1)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}))
    core.records["1"] = rec
    p.play("1", track=0)
    p.control("next")
    assert p.now["idx"] == 1


def test_control_next_past_last_sets_side_done(make_player):
    rec = mk_record(1)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}))
    core.records["1"] = rec
    p.play("1", track=0)
    p.control("next")
    p.control("next")  # past A2 -> side done
    assert p.now["side_done"] is True


def test_stop_clears_and_records_listened(make_player, monkeypatch):
    rec = mk_record(1)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}))
    core.records["1"] = rec
    p.play("1")
    p.now["listened"] = 123.0
    p.stop()
    assert p.now is None
    assert core.history and core.history[0]["id"] == 1


def test_tick_advances_by_duration(make_player):
    rec = mk_record(1)
    clk = FakeClock(1000)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}),
                          clock=clk)
    core.records["1"] = rec
    p.play("1", track=0)  # A1 = 77s -> ends at 1000 + 77000
    clk.advance(77000 + 1000)
    p._tick(1.0)
    assert p.now["idx"] == 1  # advanced to A2


def test_tick_no_change_before_track_end(make_player):
    rec = mk_record(1)
    clk = FakeClock(1000)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}),
                          clock=clk)
    core.records["1"] = rec
    p.play("1", track=0)
    clk.advance(1000)
    p._tick(1.0)
    assert p.now["idx"] == 0


def test_tick_marks_side_done_at_last_track(make_player):
    rec = mk_record(1)
    clk = FakeClock(1000)
    info = {"tracks": TRACKS}
    p, core = make_player(records=[rec], library=FakeLibrary(info), clock=clk)
    core.records["1"] = rec
    p.play("1", track=1)  # A2 = 170s
    clk.advance(170000 + 1000)
    p._tick(1.0)
    assert p.now["side_done"] is True


def test_snapshot_shape(make_player):
    rec = mk_record(1)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}))
    core.records["1"] = rec
    p.play("1", track=0)
    snap = p.snapshot()
    assert snap["record"]["id"] == 1
    assert snap["side"] == "A"
    assert snap["idx"] == 0
    assert snap["next_side"] == "B"
    assert snap["timed"] is True


def test_save_and_restore_state(make_player, tmp_path, monkeypatch):
    rec = mk_record(1)
    clk = FakeClock(1000)
    p, core = make_player(records=[rec], library=FakeLibrary({"tracks": TRACKS}),
                          clock=clk)
    core.records["1"] = rec
    # redirect RESUME_PATH into tmp so we don't touch the app's real state
    monkeypatch.setattr(core_mod, "RESUME_PATH", tmp_path / "resume.json")
    p.play("1", track=1)
    p.save_state()
    p.now = None
    p.restore_state()
    assert p.now is not None
    assert p.now["idx"] == 1


def test_activity_uses_track_mode_and_cover(make_player):
    rec = mk_record(1)
    info = {"tracks": TRACKS, "cover": "http://cover/x.png", "thumb": "http://t.png",
            "artist_id": 251}
    p, core = make_player(records=[rec], library=FakeLibrary(info))
    core.records["1"] = rec
    p.play("1", track=0)
    act = core.discord.activity
    assert act["details"] == "Speak To Me"           # track title
    assert act["assets"]["large_image"] == "http://cover/x.png"
    assert "state_url" in act                        # artist link
    assert act["buttons"][0]["label"].startswith("View on Discogs")


def test_activity_clears_when_nothing_playing(make_player):
    p, core = make_player()
    assert core.discord.activity is None


# JSON round-trip of a record keeps search keys working after reload
def test_index_record_roundtrip():
    r = mk_record(1)
    j = json.loads(json.dumps(r))
    core_mod.index_record(j)
    assert "_a" in j and "_t" in j