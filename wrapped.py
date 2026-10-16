"""A shareable "Vinyl Wrapped" picture of your listening stats, to paste into Discord.

Needs Pillow. Covers are downloaded on the spot (they're small); fonts come from Windows.
"""
import colorsys
import ctypes
import datetime as dt
import io
import os
import urllib.request

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

import stats
from core import ASSETS
from metadata import USER_AGENT

W, H, M = 1080, 1350, 80  # size and margin
BG, PANEL = (18, 16, 20), (31, 28, 34)
TEXT, MUTED, FAINT, ACCENT = (242, 235, 229), (160, 151, 143), (109, 102, 96), (239, 122, 60)


def _font(size, bold=False):
    """bold=True: semibold; bold="black": the heaviest weight, for big headings and numbers."""
    fonts = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    names = {True: ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf"), "black": ("seguibl.ttf", "segoeuib.ttf", "arialbd.ttf"),
             False: ("segoeui.ttf", "arial.ttf")}[bold]
    for name in names:
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


def period_label(period):
    today = dt.date.today()
    return {"This month": today.strftime("%B %Y"), "This year": str(today.year)}.get(period, period)


def _hours(secs):
    h = secs / 3600
    return f"{h:.0f}" if h >= 10 else f"{h:.1f}"


def _duration(secs):
    minutes = round(secs / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _art(core, rid, size):
    """A square cover (the vinyl icon when there's none), without rounded corners."""
    info = core.library.get_cached(rid) or {}
    url = info.get("cover") or info.get("thumb")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            img = Image.open(io.BytesIO(resp.read())).convert("RGB")
    except Exception:
        img = Image.open(ASSETS / "vinyl.png").convert("RGBA")
        flat = Image.new("RGB", img.size, PANEL)
        flat.paste(img, (0, 0), img)
        img = flat
    return ImageOps.fit(img, (size, size), Image.LANCZOS)


def _theme(img):
    """A bold color taken from the covers, dark enough for white text on top (like Spotify Wrapped)."""
    r, g, b = img.resize((1, 1), Image.LANCZOS).getpixel((0, 0))
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if s < 0.15:  # black-and-white covers: use the Vinyl Presence orange
        h, s = 0.06, 0.75
    r, g, b = colorsys.hsv_to_rgb(h, max(s, 0.6), 0.62)
    return int(r * 255), int(g * 255), int(b * 255)


def _paste_rounded(img, piece, xy, radius):
    mask = Image.new("L", piece.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, piece.width - 1, piece.height - 1), radius=radius, fill=255)
    img.paste(piece, xy, mask)


def _shadow(img, box, radius, blur=28, opacity=150):
    layer = Image.new("L", img.size, 0)
    ImageDraw.Draw(layer).rounded_rectangle(box, radius=radius, fill=opacity)
    layer = layer.filter(ImageFilter.GaussianBlur(blur))
    img.paste(Image.new("RGB", img.size, (0, 0, 0)), (0, 0), layer)


def _top_genre(core, period):
    since = stats.period_start(period)
    rows = [(h, s) for h, s in stats.sessions(core) if h["at"] >= since and str(h["id"]) in core.records]
    counts = {}
    for rid, _secs in stats.plays(rows):
        for genre in (core.records[str(rid)].get("genres") or [])[:2]:
            counts[genre] = counts.get(genre, 0) + 1
    return max(counts, key=counts.get) if counts else "–"


def make(core, period):
    """The picture for a stats period ("This month", "Last 30 days", "This year", "All time")."""
    data = stats.compute(core, period)
    top = data["top_records"]

    # the hero: the top 4 covers as one sleeve, with the record sliding out of it
    tile, gap = 200, 8
    art = Image.new("RGB", (tile * 2 + gap, tile * 2 + gap), BG)
    if len(top) >= 4:
        for i, (rec, _plays, _secs) in enumerate(top[:4]):
            art.paste(_art(core, rec["id"], tile), ((i % 2) * (tile + gap), (i // 2) * (tile + gap)))
    elif top:
        art = _art(core, top[0][0]["id"], art.width)
    theme = _theme(art)

    # background: the theme color fading into the dark, with record grooves
    img = Image.new("RGB", (W, H), BG)
    fade = Image.linear_gradient("L").resize((W, 820)).point(lambda v: 255 - v)
    img.paste(Image.new("RGB", (W, 820), theme), (0, 0), fade)
    grooves = Image.new("L", (W, H), 0)
    gd = ImageDraw.Draw(grooves)
    for r in range(260, 1300, 14):
        gd.ellipse((W - 160 - r, 120 - r, W - 160 + r, 120 + r), outline=22, width=2)
    fade_out = Image.linear_gradient("L").resize((W, H)).point(lambda v: max(0, 255 - v * 2))  # gone by halfway
    grooves = ImageChops.multiply(grooves, fade_out)
    img.paste(Image.new("RGB", (W, H), (255, 255, 255)), (0, 0), grooves)
    d = ImageDraw.Draw(img)

    # header
    icon = Image.open(ASSETS / "vinyl.png").convert("RGBA").resize((54, 54), Image.LANCZOS)
    img.paste(icon, (M, 54), icon)
    d.text((M + 70, 50), "Vinyl Wrapped", font=_font(40, "black"), fill=TEXT)
    label = period_label(period)
    f = _font(30, True)
    d.text((W - M - d.textlength(label, font=f), 58), label, font=f, fill=TEXT)

    if not top:
        d.text((M, 300), "Nothing played\nin this period yet.", font=_font(64, "black"), fill=TEXT, spacing=10)
    else:
        x, y = (W - art.width - 200) // 2, 140
        disc = Image.open(ASSETS / "vinyl.png").convert("RGBA").resize((art.width - 20, art.width - 20), Image.LANCZOS)
        img.paste(disc, (x + 200 + 10, y + 10), disc)
        _shadow(img, (x + 10, y + 24, x + art.width + 10, y + art.height + 24), 18)
        _paste_rounded(img, art, (x, y), 18)
        d = ImageDraw.Draw(img)

        rec, plays, secs = top[0]
        y = 584
        f = _font(22, True)
        caption = "YOUR TOP RECORD"
        d.text(((W - d.textlength(caption, font=f)) / 2, y), caption, font=f, fill=(228, 220, 214))
        f = _font(50, "black")
        title = _fit(d, rec["title"], f, W - 2 * M)
        d.text(((W - d.textlength(title, font=f)) / 2, y + 30), title, font=f, fill=TEXT)
        f = _font(28)
        sub = _fit(d, f"{rec['artist']} · {'1 play' if plays == 1 else f'{plays} plays'} · {_duration(secs)}", f, W - 2 * M)
        d.text(((W - d.textlength(sub, font=f)) / 2, y + 100), sub, font=f, fill=MUTED)

        # two top-5 columns
        col_w = (W - 2 * M - 40) // 2
        for col, (heading, rows, value) in enumerate((
                ("Most played", data["top_records"], lambda n, s: "1 play" if n == 1 else f"{n} plays"),
                ("Most time listened", data["top_time"], lambda n, s: _duration(s)))):
            cx, cy = M + col * (col_w + 40), 754
            d.text((cx, cy), heading, font=_font(32, "black"), fill=TEXT)
            cy += 54
            for rank, (r, n, s) in enumerate(rows[:5], 1):
                d.text((cx, cy - 2), str(rank), font=_font(30, "black"), fill=theme if sum(theme) > 240 else ACCENT)
                d.text((cx + 44, cy), _fit(d, r["title"], _font(26, True), col_w - 44), font=_font(26, True), fill=TEXT)
                d.text((cx + 44, cy + 32), _fit(d, f"{r['artist']} · {value(n, s)}", _font(20), col_w - 44),
                       font=_font(20), fill=MUTED)
                cy += 62

        # three numbers
        d.line((M, 1148, W - M, 1148), fill=(60, 54, 64), width=2)
        top_artist = data["top_artists"][0][0] if data["top_artists"] else "–"
        cells = [("Hours of vinyl", _hours(data["seconds"]), 54), ("Top artist", top_artist, 34),
                 ("Top genre", _top_genre(core, period), 34)]
        cell_w = (W - 2 * M) // 3
        for i, (name, val, size) in enumerate(cells):
            cx = M + i * cell_w
            d.text((cx, 1164), name, font=_font(22, True), fill=MUTED)
            f = _font(size, "black")
            d.text((cx, 1190 if size > 40 else 1202), _fit(d, val, f, cell_w - 20), font=f, fill=TEXT)

    # footer
    f = _font(22, True)
    d.text((M, H - 54), "VINYL PRESENCE", font=f, fill=FAINT)
    site = "vinylhangout.com"
    d.text((W - M - d.textlength(site, font=f), H - 54), site, font=f, fill=FAINT)
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
