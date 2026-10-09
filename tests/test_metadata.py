"""Metadata helpers: Discogs/Deezer parsing, normalization, side detection, cache."""
import json

import pytest

import metadata as m

# ---------------------------------------------------------------- normalizers

def test_norm_strips_accents_and_case():
    assert m.norm("Café Au Lait") == "cafe au lait"
    assert m.norm("  ÉLITE  ") == "elite"


def test_norm_expands_ampersand_and_junks_punct():
    assert m.norm("AC/DC & T.Rextasy") == "ac dc and t rextasy"


def test_norm_none():
    assert m.norm(None) == ""


def test_norm_core_strips_parenthetical_and_trailing_dash():
    assert m.norm_core("The Wall (2011 Remastered)") == "the wall"
    assert m.norm_core("Back In Black - 2003 Remaster") == "back in black"
    assert m.norm_core("Brain Damage") == "brain damage"


def test_clean_name_strips_discogs_disambiguator_and_asterisk():
    assert m.clean_name("Prince (2)") == "Prince"
    assert m.clean_name("Bowie*") == "Bowie"
    assert m.clean_name("Various") == "Various"
    assert m.clean_name(None) == ""


# ---------------------------------------------------------------- durations

def test_parse_duration_formats():
    assert m.parse_duration("77") == 77
    assert m.parse_duration("2:53") == 173
    assert m.parse_duration("1:02:03") == 3723


def test_parse_duration_invalid():
    assert m.parse_duration("abc") is None
    assert m.parse_duration("") is None
    assert m.parse_duration("0:00") is None
    assert m.parse_duration(None) is None


# ---------------------------------------------------------------- side detection

def test_side_of_single_letter_sides():
    assert m.side_of("A1") == "A"
    assert m.side_of("B2") == "B"
    assert m.side_of("E3") == "E"


SIDE_REGRESSION = "CD1-1 must group under 'Disc 1', not a generic 'CD' side"


def test_side_of_cd_multidisc_groups_by_disc():
    # Regression: the letter rule used to swallow the CD prefix, so every track
    # on a multi-disc CD collapsed onto one side.
    assert m.side_of("CD1-1") == "Disc 1", SIDE_REGRESSION
    assert m.side_of("CD1-2") == "Disc 1", SIDE_REGRESSION
    assert m.side_of("CD2-1") == "Disc 2", SIDE_REGRESSION


def test_side_of_numbered_top_level():
    assert m.side_of("1-1") == "Disc 1"
    assert m.side_of("1.2") == "Disc 1"
    assert m.side_of("2-7") == "Disc 2"


def test_side_of_whitespace_and_unknown():
    assert m.side_of("  CD2-3  ") == "Disc 2"
    assert m.side_of("") == "All"
    assert m.side_of(None) == "All"
    assert m.side_of("11") == "All"


# ---------------------------------------------------------------- artist names

def test_artist_names_joins_with_clean_and_joinings():
    artists = [
        {"name": "John Lennon (2)", "join": ","},
        {"name": "Paul McCartney*", "join": "&"},
        {"name": "George Harrison"},
    ]
    assert m._artist_names(artists) == "John Lennon, Paul McCartney & George Harrison"


def test_artist_names_empty():
    assert m._artist_names(None) == ""
    assert m._artist_names([]) == ""


def test_artist_names_single():
    assert m._artist_names([{"name": "Pink Floyd"}]) == "Pink Floyd"


# ---------------------------------------------------------------- fetch_discogs parsing

def test_fetch_discogs_parses_tracklist_and_expands_index_tracks(monkeypatch):
    payload = {
        "artists": [{"name": "Pink Floyd", "id": 251}, {"name": "Guest"}],
        "year": 1973,
        "images": [{"type": "primary", "uri": "http://cover/", "uri150": "http://t/"}],
        "tracklist": [
            {"type_": "track", "position": "A1", "title": "Speak To Me", "duration": "1:17"},
            {"type_": "index", "position": "B", "title": "Side Two", "sub_tracks": [
                {"type_": "track", "position": "B1", "title": "Us And Them", "duration": "7:49"}]},
            {"type_": "not-track", "position": "X", "title": "skipme"},
        ],
    }
    monkeypatch.setattr(m, "_get_json", lambda url, headers=None, timeout=10: payload)
    info = m.fetch_discogs("123")
    assert info["artist"] == "Pink Floyd, Guest"
    assert info["year"] == 1973
    assert info["cover"] == "http://cover/"
    # index track's sub-track expanded; not-track entries dropped
    assert [t["title"] for t in info["tracks"]] == ["Speak To Me", "Us And Them"]
    assert info["tracks"][1]["side"] == "B"
    assert info["tracks"][0]["duration"] == 77


def test_fetch_discogs_single_artist_id():
    payload = {"artists": [{"name": "Solo", "id": 7}], "tracklist": [],
               "images": [{"type": "primary", "uri": "c"}], "year": 2020}
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(m, "_get_json", lambda url, headers=None, timeout=10: payload)
    info = m.fetch_discogs("1")
    assert info["artist_id"] == 7
    monkeypatch.undo()


# ---------------------------------------------------------------- library cache

def test_library_saves_and_reloads(tmp_path):
    lib = m.Library(tmp_path / "releases.json", lambda: "")
    lib.cache = {"9": {"cover": "c", "tracks": [], "artist_id": None}}
    lib._dirty = 1
    lib.save()
    assert (tmp_path / "releases.json").exists()
    # a fresh instance loads it back
    lib2 = m.Library(tmp_path / "releases.json", lambda: "")
    assert lib2.cache["9"]["cover"] == "c"


def test_library_save_deferred_until_dirty_threshold(tmp_path):
    lib = m.Library(tmp_path / "releases.json", lambda: "")
    lib.save(force=False)  # _dirty starts 0
    assert not (tmp_path / "releases.json").exists()
    lib._dirty = 9
    lib.save(force=False)
    assert not (tmp_path / "releases.json").exists()
    lib._dirty = 10
    lib.save(force=False)
    assert (tmp_path / "releases.json").exists()


def test_library_corrupt_cache_ignored(tmp_path):
    p = tmp_path / "releases.json"
    p.write_text("not json{{{", "utf-8")
    lib = m.Library(p, lambda: "")
    assert lib.cache == {}


def test_library_version_mismatch_discards(tmp_path):
    p = tmp_path / "releases.json"
    p.write_text(json.dumps({"_v": m.CACHE_VERSION - 1, "releases": {"5": {"cover": "x"}}}), "utf-8")
    lib = m.Library(p, lambda: "")
    assert "5" not in lib.cache


def test_library_tmp_cleanup_on_save(tmp_path):
    lib = m.Library(tmp_path / "releases.json", lambda: "")
    lib.cache = {"1": {"cover": "c"}}
    lib.save()
    assert not (tmp_path / "releases.json.tmp").exists()


# ---------------------------------------------------------------- fill_durations

def test_fill_durations_matches_by_title_and_core():
    tracks = [
        {"title": "Speak To Me", "duration": None},
        {"title": "Breathe Remix (2003)", "duration": None},
    ]
    # deezer_tracks are (title, seconds) pairs
    deezer = [("Speak To Me", 77), ("Breathe Remix", 170)]
    filled = m.fill_durations(tracks, deezer)
    assert filled == 2
    assert tracks[0]["duration"] == 77
    assert tracks[1]["duration"] == 170


def test_fill_durations_positional_fallback_when_same_length():
    tracks = [{"title": "X", "duration": None}]
    deezer = [("not matching", 42)]
    # single track, no textual match, equal length -> positional fallback fills it
    assert m.fill_durations(tracks, deezer) == 1
    assert tracks[0]["duration"] == 42
    # equal length + no textual match -> fallback by position
    tracks = [{"title": "A", "duration": None}, {"title": "B", "duration": None}]
    deezer = [("q", 11), ("z", 22)]
    assert m.fill_durations(tracks, deezer) == 2
    assert tracks[0]["duration"] == 11
    assert tracks[1]["duration"] == 22


def test_fill_durations_noop_on_empty():
    assert m.fill_durations([{"title": "x", "duration": None}], []) == 0