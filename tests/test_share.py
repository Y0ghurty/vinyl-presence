"""Webhook validation, formatting escapes, and the share message builder."""
import pytest

import share as share_mod

# ---------------------------------------------------------------- webhook validation

def test_valid_webhook_accepts_discord_urls():
    good = [
        "https://discord.com/api/webhooks/123456789012345678/AbC123xyz",
        "https://discordapp.com/api/webhooks/111/abc",
        "https://ptb.discord.com/api/webhooks/111/abc",
        "https://canary.discordapp.com/api/v10/webhooks/111/abc-",
    ]
    for u in good:
        assert share_mod.valid_webhook(u), u


def test_valid_webhook_rejects_non_discord():
    bad = [
        "https://example.com/hook",
        "http://discord.com/api/webhooks/111/abc",
        "https://discord.com/not-a-webhook",
        "not a url",
        "",
        None,
    ]
    for u in bad:
        assert not share_mod.valid_webhook(u), repr(u)


def test_valid_webhook_strips_whitespace():
    assert share_mod.valid_webhook("  https://discord.com/api/webhooks/1/abc  ")


# ---------------------------------------------------------------- escaping

def test_escape_handles_discord_formatting_chars():
    assert share_mod._escape("AC*DC") == r"AC\*DC"
    assert share_mod._escape("It's_fine_") == r"It's\_fine\_"
    assert share_mod._escape("a`code`b") == r"a\`code\`b"
    assert share_mod._escape("|split|") == r"\|split\|"
    assert share_mod._escape("plain") == "plain"
    assert share_mod._escape(None) == ""


# ---------------------------------------------------------------- message builder

def test_post_record_builds_embed(monkeypatch):
    captured = {}
    monkeypatch.setattr(share_mod, "_post", lambda url, payload: captured.update(
        {"url": url, "payload": payload}))
    rec = {"id": 123, "title": "The Wall", "artist": "Pink Floyd", "year": "1979",
           "label": "Harvest", "format": "Vinyl, LP, Album"}
    info = {"cover": "http://cover/x.png"}
    share_mod.post_record("https://discord.com/api/webhooks/1/abc", rec, info, {})
    payload = captured["payload"]
    embed = payload["embeds"][0]
    assert embed["title"] == "The Wall"
    assert embed["url"] == "https://www.discogs.com/release/123"
    assert embed["image"]["url"] == "http://cover/x.png"
    assert embed["author"]["name"] == "Now spinning"
    assert payload["allowed_mentions"] == {"parse": []}


def test_post_record_strips_vinyl_prefix_from_details(monkeypatch):
    captured = {}
    monkeypatch.setattr(share_mod, "_post", lambda url, payload: captured.update(payload=payload))
    rec = {"id": 1, "title": "T", "artist": "A", "year": "1979", "label": "L",
           "format": "Vinyl, LP, Album"}
    share_mod.post_record("https://discord.com/api/webhooks/1/x", rec, {}, {})
    desc = captured["payload"]["embeds"][0]["description"]
    assert "Vinyl, LP" not in desc
    assert "LP, Album" in desc or "LP" in desc


def test_post_record_no_cover_omits_image(monkeypatch):
    captured = {}
    monkeypatch.setattr(share_mod, "_post", lambda url, payload: captured.update(payload=payload))
    rec = {"id": 1, "title": "T", "artist": "A", "year": "", "label": "", "format": "CD"}
    share_mod.post_record("https://discord.com/api/webhooks/1/x", rec, {}, {})
    assert "image" not in captured["payload"]["embeds"][0]


def test_sender_uses_your_name_unless_invalid(monkeypatch):
    captured = {}
    monkeypatch.setattr(share_mod, "_post", lambda url, payload: captured.update(payload=payload))
    share_mod.post_test("https://discord.com/api/webhooks/1/x", {"user": "Claude"})
    assert captured["payload"]["username"] == "Claude"
    # "discord" / "clyde" names are refused by Discord -> fallback
    share_mod.post_test("https://discord.com/api/webhooks/1/x", {"user": "Clyde Bot"})
    assert captured["payload"]["username"] == "Vinyl Presence"


def test_post_invalid_webhook_raises():
    rec = {"id": 1, "title": "T", "artist": "A", "year": "", "label": "", "format": ""}
    with pytest.raises(ValueError):
        share_mod.post_record("https://evil.example/hook", rec, {}, {})