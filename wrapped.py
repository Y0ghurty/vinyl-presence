"""A shareable "Vinyl Wrapped" picture of your listening stats, to paste into Discord.

Needs Pillow. Covers are downloaded on the spot (they're small); fonts come from Windows.
"""
import ctypes
import datetime as dt
import io
import os
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageOps

import stats
from core import ASSETS
from metadata import USER_AGENT

W, H, M = 1080, 1350, 80  # size and margin
BG, PANEL = (18, 16, 20), (31, 28, 34)
TEXT, MUTED, FAINT, ACCENT = (242, 235, 229), (160, 151, 143), (109, 102, 96), (239, 122, 60)


def _font(size, bold=False):
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in (("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(os.path.join(fonts, name), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _fit(draw, text, font, width):
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=radius, fill=255)
    out = Image.new("RGB", img.size, PANEL)
    out.paste(img, (0, 0), mask)
    return out


def _cover(core, rid, size):
    info = core.library.get_cached(rid) or {}
    url = info.get("cover") or info.get("thumb")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception:  # no cover (or offline): the vinyl icon instead
        img = Image.open(ASSETS / "vinyl.png").convert("RGBA")
        flat = Image.new("RGB", img.size, PANEL)
        flat.paste(img, (0, 0), img)
        img = flat
    return _rounded(ImageOps.fit(img, (size, size), Image.LANCZOS), 14)


def period_label(period):
    today = dt.date.today()
    return {"This month": today.strftime("%B %Y"), "This year": str(today.year)}.get(period, period)


def _hours(secs):
    h = secs / 3600
    return f"{h:.0f}" if h >= 10 else f"{h:.1f}"


def _duration(secs):
    minutes = round(secs / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def make(core, period):
    """The picture for a stats period ("This month", "Last 30 days", "This year", "All time")."""
    data = stats.compute(core, period)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    covers = {}

    def cover(rid, size):
        if (rid, size) not in covers:
            covers[rid, size] = _cover(core, rid, size)
        return covers[rid, size]

    # header: vinyl + title + period
    icon = Image.open(ASSETS / "vinyl.png").convert("RGBA").resize((84, 84), Image.LANCZOS)
    img.paste(icon, (M, M), icon)
    d.text((M + 108, M - 4), "My Vinyl Wrapped", font=_font(58, True), fill=TEXT)
    d.text((M + 110, M + 64), period_label(period), font=_font(32), fill=ACCENT)

    # the big number
    y = 196
    d.text((M - 5, y), _hours(data["seconds"]), font=_font(128, True), fill=ACCENT)
    d.text((M, y + 152), "hours of vinyl", font=_font(34), fill=MUTED)

    # three tiles
    y = 412
    top_artist = data["top_artists"][0][0] if data["top_artists"] else "–"
    tiles = [(str(data["sessions"]), "records played"), (str(data["records"]), "different records"),
             (top_artist, "top artist")]
    tile_w = (W - 2 * M - 2 * 24) // 3
    for i, (value, label) in enumerate(tiles):
        x = M + i * (tile_w + 24)
        d.rounded_rectangle((x, y, x + tile_w, y + 122), radius=18, fill=PANEL)
        big = _font(46 if len(value) <= 6 else 32, True)
        d.text((x + 26, y + 18), _fit(d, value, big, tile_w - 52), font=big, fill=TEXT)
        d.text((x + 26, y + 78), label, font=_font(24), fill=FAINT)

    # two top-3 lists: by plays, then by time listened
    def top_list(y, heading, rows, value):
        d.text((M, y), heading, font=_font(26, True), fill=FAINT)
        y += 44
        title_f, artist_f, value_f = _font(32, True), _font(26), _font(28, True)
        for rec, plays, secs in rows:
            img.paste(cover(rec["id"], 84), (M, y))
            text_x = M + 108
            label = value(plays, secs)
            room = W - M - text_x - d.textlength(label, font=value_f) - 30
            d.text((text_x, y + 2), _fit(d, rec["title"], title_f, room), font=title_f, fill=TEXT)
            d.text((text_x, y + 46), _fit(d, rec["artist"], artist_f, room), font=artist_f, fill=MUTED)
            d.text((W - M - d.textlength(label, font=value_f), y + 24), label, font=value_f, fill=ACCENT)
            y += 100

    if not data["top_records"]:
        d.text((M, 590), "Nothing played in this period yet.", font=_font(32), fill=MUTED)
    else:
        top_list(570, "MOST PLAYED", data["top_records"][:3],
                 lambda plays, _secs: "1 play" if plays == 1 else f"{plays} plays")
        top_list(932, "MOST TIME LISTENED", data["top_time"][:3], lambda _plays, secs: _duration(secs))

    # footer
    foot = "Made with Vinyl Presence  ·  vinylhangout.com"
    f = _font(24)
    d.text(((W - d.textlength(foot, font=f)) / 2, H - 52), foot, font=f, fill=FAINT)
    return img


def copy_to_clipboard(img):
    """Put the picture on the Windows clipboard, so Ctrl+V pastes it into Discord."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]  # a DIB is a BMP file without its 14-byte file header
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    k32.GlobalAlloc.restype = ctypes.c_void_p
    k32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    u32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    u32.SetClipboardData.restype = ctypes.c_void_p
    handle = k32.GlobalAlloc(0x0042, len(data))  # GMEM_MOVEABLE | GMEM_ZEROINIT
    ctypes.memmove(k32.GlobalLock(handle), data, len(data))
    k32.GlobalUnlock(handle)
    if not u32.OpenClipboard(None):
        raise OSError("The clipboard is in use by another app. Try again.")
    try:
        u32.EmptyClipboard()
        if not u32.SetClipboardData(8, handle):  # CF_DIB
            raise OSError("Couldn't put the picture on the clipboard.")
    finally:
        u32.CloseClipboard()
