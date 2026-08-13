# pattern: Functional Core
"""Lay out and draw a single 9:16 skeet frame.

Pure with respect to the filesystem and the network: every function here takes
already-loaded fonts and an already-decoded avatar image and returns pixels.
Fetching, muxing, and animation live in the imperative shell.

The two things most avatar renderers cannot do, and which this project is
mostly *about*, are both here: paragraph wrapping, and choosing a type size that
makes a post of unknown length fill the frame.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# 9:16 at the resolution Shorts/Reels/TikTok all accept without re-encoding.
CW, CH = 1080, 1920

# YouTube Shorts paints its own chrome over the video: title and channel name
# bottom-left, the action rail (like/share/remix) down the right, progress bar
# along the bottom. Content outside this box gets covered on the surface we
# launch on, which is the most common way an otherwise-good short reads amateur.
SAFE = (64, 190, 1016, 1560)  # l, t, r, b

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"


@dataclass(frozen=True)
class Palette:
    """Bluesky's own dark mode, so a render reads as native to the source."""

    bg: tuple[int, int, int] = (9, 12, 17)
    bg_lift: tuple[int, int, int] = (20, 27, 36)
    card: tuple[int, int, int] = (22, 30, 39)
    hairline: tuple[int, int, int] = (44, 57, 71)
    text: tuple[int, int, int] = (242, 246, 250)
    muted: tuple[int, int, int] = (126, 144, 163)
    accent: tuple[int, int, int] = (0, 133, 255)


@dataclass(frozen=True)
class Author:
    display_name: str
    handle: str
    followers: int | None = None


@dataclass(frozen=True)
class Attribution:
    """Someone's words together with whose they are.

    This exists so that no function anywhere takes the words and the name as two
    separate arguments. An earlier make_video took --handle and --text
    independently and put one person's words on screen under another person's
    name, in a project about asking before reusing what people wrote. post.py
    fixed that at the fetch boundary and compose() quietly reintroduced it, one
    loose parameter each.

    Build it with `of()`, which takes the whole post record and is the only
    supported way in. Do not add a constructor that accepts a bare handle.
    """

    text: str
    author: Author

    @classmethod
    def of(cls, post) -> Attribution:
        """From a post record — anything carrying text, display_name and handle.

        Duck-typed rather than importing post.Post, which would make this
        filesystem- and network-adjacent module a dependency of the pure one.
        """
        return cls(post.text, Author(post.display_name, post.handle))


@functools.lru_cache(maxsize=64)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Memoised: compose() asks for the same two faces on every one of a few
    hundred frames, and this module's own docstring promises callers get
    already-loaded fonts. Same reasoning as _FIT_CACHE below, which fixed this
    bug class for fit_text and was never applied to the loader itself."""
    return ImageFont.truetype(str(FONTS / name), size)


def _glyph_sig(font: ImageFont.FreeTypeFont, ch: str) -> tuple | None:
    """A fingerprint of what the face actually draws for `ch`."""
    try:
        m = font.getmask(ch)
        return (font.getbbox(ch), m.size, sum(bytes(m)))
    except Exception:
        return None


def unsupported_chars(text: str, font: ImageFont.FreeTypeFont) -> set[str]:
    """Characters the face has no glyph for, and will therefore draw as tofu.

    PIL performs no font fallback, so a Japanese, Korean or Arabic post silently
    rasterises as a row of identical empty rectangles — under the author's real
    handle and profile picture. Refusing is the only honest option; shipping
    boxes is worse than not rendering.

    Missing codepoints all resolve to the face's .notdef glyph, so they share a
    fingerprint with codepoints guaranteed never to be mapped. Astral-plane
    characters (emoji, mostly) are checked separately: they do not always land
    on .notdef, and the bundled faces are Latin-only regardless.

    An earlier version of this compared `getmask(...).tobytes()`, which does not
    exist on ImagingCore — the AttributeError was swallowed and every script on
    earth reported clean. A guard that cannot fail closed is not a guard.

    Which is why the empty-sentinel case raises. If Pillow ever stops returning a
    mask for the two non-characters, `notdefs` comes out empty, `sig in notdefs`
    is never true, and the guard silently passes every script on earth again \u2014
    the exact failure it was rewritten to stop, one Pillow release away under a
    floor-only version pin.
    """
    notdefs = {sig for c in ("\ufffe", "\uffff") if (sig := _glyph_sig(font, c))}
    if not notdefs:
        raise RuntimeError(
            "cannot fingerprint the .notdef glyph \u2014 the tofu guard would pass "
            "every script silently, so refusing to render rather than risk empty "
            "boxes under a real author's name. Check the Pillow version."
        )
    bad = set()
    for ch in set(text):
        if ch.isspace():
            continue
        if ord(ch) > 0xFFFF:
            bad.add(ch)
            continue
        sig = _glyph_sig(font, ch)
        if sig is None or sig in notdefs:
            bad.add(ch)
    return bad


def _break_token(
    word: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw
) -> list[str]:
    """Split a single token too wide to fit, at the character level.

    URLs and long hashtags are ordinary skeet content and contain no spaces, so
    word wrapping alone cannot bound them — the old code emitted such a token on
    its own line whatever its width, and a normal archive.org link measured
    2817px inside a 952px box, drawn centred from x = -868.
    """
    out, cur = [], ""
    for ch in word:
        if cur and draw.textlength(cur + ch, font=font) > max_w:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


def wrap(
    text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw
) -> list[str]:
    """Greedy word wrap. Preserves author line breaks, which carry comic timing."""
    lines: list[str] = []
    paras = text.split("\n")
    while len(paras) > 1 and not paras[-1].strip():
        paras.pop()  # a trailing newline ate a whole line of vertical budget
    for para in paras:
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
                continue
            # The line has to break here. Flush it, then place the word — and if
            # the word alone still will not fit, split it at the character level.
            # Testing "is cur empty" first was wrong: a URL arriving after two
            # short words took the plain branch and landed unbroken on its line.
            if cur:
                lines.append(cur)
                cur = ""
            if draw.textlength(word, font=font) > max_w:
                pieces = _break_token(word, font, max_w, draw)
                lines.extend(pieces[:-1])
                cur = pieces[-1]
            else:
                cur = word
        if cur:
            lines.append(cur)
    return lines


# Every input to fit_text is fixed for the length of a render, but compose() is
# called per frame — so a 10s/24fps video ran the same binary search 240 times,
# reloading the font from disk on each probe. build_poses already caches the
# figure for exactly this reason; the text layer never got the same treatment.
_FIT_CACHE: dict[tuple, tuple] = {}


def fit_text(
    text: str,
    font_name: str,
    box: tuple[int, int],
    draw: ImageDraw.ImageDraw,
    *,
    lo: int = 34,
    hi: int = 104,
    leading: float = 1.26,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """Largest size at which `text` fits `box`, by binary search.

    This is what keeps a 12-word post and a 60-word post both looking composed.
    A fixed size would leave the short one swimming and clip the long one.
    """
    key = (text, font_name, box, lo, hi, leading)
    if key in _FIT_CACHE:
        return _FIT_CACHE[key]

    max_w, max_h = box
    best: tuple[ImageFont.FreeTypeFont, list[str], int] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(font_name, mid)
        lines = wrap(text, font, max_w, draw)
        line_h = int(mid * leading)
        widest = max((draw.textlength(ln, font=font) for ln in lines), default=0)
        if line_h * len(lines) <= max_h and widest <= max_w:
            best = (font, lines, line_h)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:  # pathological: even `lo` overflows, accept the overflow
        font = load_font(font_name, lo)
        best = (font, wrap(text, font, max_w, draw), int(lo * leading))
    best = (best[0], list(best[1]), best[2])
    _FIT_CACHE[key] = best
    return (best[0], list(best[1]), best[2])


def draw_paragraph(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    xy: tuple[int, int],
    font: ImageFont.FreeTypeFont,
    line_h: int,
    fill: tuple[int, int, int],
) -> int:
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return y


def circle_avatar(src: Image.Image, size: int, ring: tuple[int, int, int] | None = None) -> Image.Image:
    """Center-crop to square, mask to a circle, optionally ring it.

    A plain centre crop, which is the only option that generalises: every
    avatar is someone else's, and none are hand-marked.
    """
    w, h = src.size
    side = min(w, h)
    src = src.crop(
        ((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side)
    ).resize((size, size), Image.LANCZOS).convert("RGBA")

    ss = 4  # supersample the mask so the edge is not a staircase
    mask = Image.new("L", (size * ss, size * ss), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * ss - 1, size * ss - 1], fill=255)
    src.putalpha(mask.resize((size, size), Image.LANCZOS))

    if ring is None:
        return src
    pad = max(3, size // 40)
    out = Image.new("RGBA", (size + pad * 2, size + pad * 2), (0, 0, 0, 0))
    rmask = Image.new("L", ((size + pad * 2) * ss, (size + pad * 2) * ss), 0)
    ImageDraw.Draw(rmask).ellipse(
        [0, 0, (size + pad * 2) * ss - 1, (size + pad * 2) * ss - 1], fill=255
    )
    ring_layer = Image.new("RGBA", out.size, (*ring, 255))
    ring_layer.putalpha(rmask.resize(out.size, Image.LANCZOS))
    out.alpha_composite(ring_layer)
    out.alpha_composite(src, (pad, pad))
    return out


def backdrop(pal: Palette, avatar: Image.Image | None = None) -> Image.Image:
    """Vignette lift, optionally tinted by a blurred blow-up of the avatar.

    Pulling the background out of the author's own pfp means every render is
    keyed to its subject without anyone picking a color.
    """
    bg = Image.new("RGB", (CW, CH), pal.bg)
    if avatar is not None:
        # Stretch to the full canvas rather than pasting a square: a square
        # leaves hard seams at its top and bottom edges that survive the blur.
        # Aspect distortion is invisible under a 160px radius.
        tint = avatar.convert("RGB").resize((CW, CH), Image.LANCZOS).filter(
            ImageFilter.GaussianBlur(160)
        )
        bg = Image.blend(bg, tint, 0.34)

    v = Image.new("L", (CW, CH), 0)
    ImageDraw.Draw(v).ellipse([CW * -0.25, CH * 0.06, CW * 1.25, CH * 0.94], fill=90)
    lift = Image.new("RGB", (CW, CH), pal.bg_lift)
    return Image.composite(lift, bg, v.filter(ImageFilter.GaussianBlur(220)))


def rounded_card(
    size: tuple[int, int], radius: int, pal: Palette, alpha: int = 255
) -> Image.Image:
    card = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], radius, fill=(*pal.card, alpha))
    d.rounded_rectangle(
        [0, 0, size[0] - 1, size[1] - 1], radius, outline=(*pal.hairline, alpha), width=2
    )
    return card


def ellipsize(
    text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw
) -> str:
    """Truncate to fit. Display names and handles are user-controlled and long
    ones are common, so every identity draw needs a width bound."""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ell, font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + ell


def draw_identity(
    draw: ImageDraw.ImageDraw,
    author: Author,
    xy: tuple[int, int],
    pal: Palette,
    *,
    name_size: int = 42,
    handle_size: int = 36,
    max_w: int | None = None,
) -> None:
    x, y = xy
    bound = max_w if max_w is not None else SAFE[2] - x
    name_f = load_font("Inter-Bold.ttf", name_size)
    handle_f = load_font("Inter-Regular.ttf", handle_size)
    draw.text((x, y), ellipsize(author.display_name, name_f, bound, draw), font=name_f, fill=pal.text)
    draw.text(
        (x, y + int(name_size * 1.24)),
        ellipsize(f"@{author.handle}", handle_f, bound, draw),
        font=handle_f,
        fill=pal.muted,
    )
