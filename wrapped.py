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
from version import GITHUB_REPO

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


def make(core, period):
    """The picture for a stats period ("This month", "Last 30 days", "This year", "All time")."""
    data = stats.compute(core, period)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # header: vinyl + title + period
    icon = Image.open(ASSETS / "vinyl.png").convert("RGBA").resize((84, 84), Image.LANCZOS)
    img.paste(icon, (M, M), icon)
    d.text((M + 108, M - 4), "My Vinyl Wrapped", font=_font(58, True), fill=TEXT)
    d.text((M + 110, M + 64), period_label(period), font=_font(32), fill=ACCENT)

    # the big number
    y = 220
    d.text((M - 6, y), _hours(data["seconds"]), font=_font(150, True), fill=ACCENT)
    d.text((M, y + 178), "hours of vinyl", font=_font(36), fill=MUTED)

    # three tiles
    y = 490
    top_artist = data["top_artists"][0][0] if data["top_artists"] else "–"
    tiles = [(str(data["sessions"]), "records played"), (str(data["records"]), "different records"),
             (top_artist, "top artist")]
    tile_w = (W - 2 * M - 2 * 24) // 3
    for i, (value, label) in enumerate(tiles):
        x = M + i * (tile_w + 24)
        d.rounded_rectangle((x, y, x + tile_w, y + 140), radius=18, fill=PANEL)
        big = _font(50 if len(value) <= 6 else 34, True)
        d.text((x + 26, y + 22), _fit(d, value, big, tile_w - 52), font=big, fill=TEXT)
        d.text((x + 26, y + 92), label, font=_font(26), fill=FAINT)

    # most played records
    y = 670
    d.text((M, y), "MOST PLAYED", font=_font(26, True), fill=FAINT)
    y += 44
    title_f, artist_f, count_f = _font(36, True), _font(28), _font(30, True)
    rows = data["top_records"][:4]
    if not rows:
        d.text((M, y + 10), "Nothing played in this period yet.", font=_font(32), fill=MUTED)
    for rank, (rec, plays, _secs) in enumerate(rows, 1):
        img.paste(_cover(core, rec["id"], 96), (M, y))
        text_x = M + 124
        count = f"{plays}×"
        count_w = d.textlength(count, font=count_f)
        d.text((text_x, y + 8), _fit(d, rec["title"], title_f, W - M - text_x - count_w - 30), font=title_f, fill=TEXT)
        d.text((text_x, y + 54), _fit(d, rec["artist"], artist_f, W - M - text_x - count_w - 30), font=artist_f,
               fill=MUTED)
        d.text((W - M - count_w, y + 30), count, font=count_f, fill=ACCENT)
        y += 112

    # top artists
    if data["top_artists"]:
        y = 1172
        names = "  ·  ".join(name for name, _p, _s in data["top_artists"][:3])
        d.text((M, y), "TOP ARTISTS", font=_font(26, True), fill=FAINT)
        d.text((M, y + 36), _fit(d, names, _font(30), W - 2 * M), font=_font(30), fill=TEXT)

    # footer
    foot = f"Made with Vinyl Presence  ·  github.com/{GITHUB_REPO}" if GITHUB_REPO else "Made with Vinyl Presence"
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
