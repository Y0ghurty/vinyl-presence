"""Post "now spinning" messages to a Discord channel through a webhook.

A webhook is a URL a server admin creates for one channel (Channel settings →
Integrations → Webhooks). Anyone who has the URL can post there, so it's kept
in your local config.json and never shared by the app.
"""
import json
import re
import time
import urllib.request

from metadata import USER_AGENT

WEBHOOK_RE = re.compile(r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/\d+/[\w-]+$")
ACCENT = 0xEF7A3C


def valid_webhook(url):
    return bool(WEBHOOK_RE.match((url or "").strip()))


def _escape(text):
    """Keep artist/album names from being read as Discord formatting (*bold*, _italic_, ...)."""
    return re.sub(r"([*_~`|>\\])", r"\\\1", text or "")


def _sender(status):
    """Post under your own Discord name and avatar, like a message from you."""
    name = (status or {}).get("user") or ""
    if not name or re.search("discord|clyde", name, re.I):  # Discord refuses these in webhook names
        name = "Vinyl Presence"
    sender = {"username": name[:80]}
    if (status or {}).get("avatar"):
        sender["avatar_url"] = status["avatar"]
    return sender


def _post(url, payload):
    if not valid_webhook(url):
        raise ValueError("That isn't a Discord webhook URL.")
    req = urllib.request.Request(url.strip(), data=json.dumps(payload).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def post_record(url, rec, info, status):
    fmt = rec["format"][7:] if rec["format"].startswith("Vinyl, ") else rec["format"]
    details = " · ".join(x for x in (rec["year"], rec["label"], fmt) if x)
    embed = {
        "author": {"name": "Now spinning"},
        "title": rec["title"][:256],
        "url": f"https://www.discogs.com/release/{rec['id']}",
        "description": f"**{_escape(rec['artist'])}**" + (f"\n{_escape(details)}" if details else ""),
        "color": ACCENT,
        "footer": {"text": "Vinyl Presence"},
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    cover = info.get("cover") or info.get("thumb")
    if cover:
        embed["image"] = {"url": cover}
    _post(url, {**_sender(status), "embeds": [embed], "allowed_mentions": {"parse": []}})


def post_test(url, status):
    _post(url, {**_sender(status), "allowed_mentions": {"parse": []},
                "content": "Vinyl Presence is connected to this channel. Records you play will show up here."})
