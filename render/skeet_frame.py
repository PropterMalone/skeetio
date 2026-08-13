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


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def wrap(
    text: str, font: ImageFont.FreeTypeFont, max_w: int, draw: ImageDraw.ImageDraw
) -> list[str]:
    """Greedy word wrap. Preserves author line breaks, which carry comic timing."""
    lines: list[str] = []
    for para in text.split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for word in para.split():
            trial = f"{cur} {word}".strip()
            if draw.textlength(trial, font=font) <= max_w or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


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
    max_w, max_h = box
    best: tuple[ImageFont.FreeTypeFont, list[str], int] | None = None
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(font_name, mid)
        lines = wrap(text, font, max_w, draw)
        line_h = int(mid * leading)
        if line_h * len(lines) <= max_h:
            best = (font, lines, line_h)
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:  # pathological: even `lo` overflows, accept the overflow
        font = load_font(font_name, lo)
        best = (font, wrap(text, font, max_w, draw), int(lo * leading))
    return best


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
