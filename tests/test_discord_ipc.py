"""Discord IPC framing and compatibility downgrade logic (no live socket)."""
import io
import json
import struct

import discord_ipc as d


class FakeConn:
    """In-memory binary pipe backing DiscordIPC._conn."""

    def __init__(self):
        self.buf = io.BytesIO()

    def write(self, data):
        self.buf.write(data)

    def read(self, n):
        return self.buf.read(n)


def _frame(op, payload):
    body = json.dumps(payload).encode("utf-8")
    return struct.pack("<II", op, len(body)) + body


# ---------------------------------------------------------------- framing

def test_send_roundtrips_op_and_length():
    conn = FakeConn()
    ipc = d.DiscordIPC("123")
    ipc._conn = conn
    ipc._send(d.OP_FRAME, {"cmd": "SET_ACTIVITY", "args": {}})
    conn.buf.seek(0)
    op, length = struct.unpack("<II", conn.buf.read(8))
    assert op == d.OP_FRAME
    body = conn.buf.read(length)
    assert json.loads(body)["cmd"] == "SET_ACTIVITY"


def test_recv_parses_op_and_payload():
    ipc = d.DiscordIPC("123")
    ipc._conn = FakeConn()
    ipc._conn.buf.write(_frame(d.OP_FRAME, {"evt": "READY", "data": {"user": {"id": "1"}}}))
    ipc._conn.buf.seek(0)
    op, data = ipc._recv()
    assert op == d.OP_FRAME
    assert data["evt"] == "READY"


# ---------------------------------------------------------------- compatibility downgrade

def test_downgrade_level_0_is_identity():
    act = {"name": "x", "details": "d", "assets": {"large_image": "c"}}
    assert d._downgrade(act, 0) == act


def test_downgrade_removes_newer_fields():
    act = {"name": "x", "status_display_type": 1, "details_url": "u", "state_url": "s",
           "details": "d", "assets": {"large_image": "c", "large_url": "u", "small_url": "u2"}}
    out = d._downgrade(act, 1)
    for k in ("name", "status_display_type", "details_url", "state_url"):
        assert k not in out
    assert out["assets"]["large_image"] == "c"
    assert "large_url" not in out["assets"]
    assert "small_url" not in out["assets"]
    assert out["details"] == "d"


def test_downgrade_level_2_removes_type_and_buttons():
    act = {"type": 2, "buttons": [{"label": "x"}], "details": "d"}
    out = d._downgrade(act, 2)
    assert "type" not in out
    assert "buttons" not in out
    assert out["details"] == "d"


def test_downgrade_none_and_assets_missing():
    assert d._downgrade(None, 1) is None
    out = d._downgrade({"name": "x"}, 1)
    assert "assets" not in out