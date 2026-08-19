# pattern: Functional Core
"""Compositions that put a skeet over moving footage.

The governing idea: the b-roll does not have to be *about* anything.
It has to move. Motion holds the eye long enough to finish reading, which is the
entire reason karaoke videos put footage behind lyrics. Relevance is a bonus.

The register is affirmation-poster — grand type, earnest archival imagery,
throwaway content. The mismatch is the joke and it writes itself once the
pairing exists.
"""

from __future__ import annotations

import colorsys
import functools
import hashlib
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from skeet_frame import SAFE, Attribution, circle_avatar, ellipsize, fit_text, load_font


@dataclass(frozen=True)
class Mood:
    """Colours for the text layer. Deliberately warm and light: the footage is
    century-old monochrome, and a cool overlay makes it read as dead rather
    than dreamy."""

    ink: tuple[int, int, int] = (255, 251, 244)
    ink_shadow: tuple[int, int, int] = (18, 14, 22)
    rule: tuple[int, int, int] = (236, 214, 176)
    credit: tuple[int, int, int] = (214, 206, 196)


# The author's picture, at rest in the bottom-left corner. It used to ride an
# animated creature that could bob, lean and point; that figure is parked, not
# rejected — see docs/design.md for why and how to bring it back. Module-level
# because the safe-area tests reproduce this placement exactly, and a copy of
# the number in the test would pass while the render drifted away from it.
PFP = 160
PFP_GAP = 22  # disc to the identity type set beside it

# Credit type size, its gutter, and the column reserved for it outright.
#
# This used to be derived from whatever the creature left over: at its widest,
# 646px starting from a centre at 0.30 of the frame, the column came to 346px,
# and 20pt was forced by "Prelinger Archives · public domain" measuring 322px at
# 20pt against 354px at 22pt. The disc is 160px and sits at the safe-area edge,
# so there is no longer a moving obstacle to measure against. The column is now
# declared rather than inferred: 380px still holds the rights line unellipsized
# at 20pt, with headroom the creature never allowed.
#
# Raising CREDIT_PT or lowering CREDIT_COL silently pushes the one line making a
# legal claim back out of its column. tests/test_safe_area.py holds that.
#
# 22pt rather than the old 20: the rights line measures 354px, which did not fit
# the 346px the creature left but does fit here. It was set at the size the
# obstacle allowed, and at that size it was too faint to actually read back on a
# rendered frame — a credit nobody can read is the same failure as a credit
# outside the safe area, just less obvious.
CREDIT_PT = 22
CREDIT_GUTTER = 24
CREDIT_COL = 380

# What is left for the display name and handle beside the disc, once the credit
# has its column: 1016 - 380 - 24 - (64 + 160 + 22) = 366.
IDENT_COL = SAFE[2] - CREDIT_COL - CREDIT_GUTTER - (SAFE[0] + PFP + PFP_GAP)


@functools.lru_cache(maxsize=8)
def _falloff(w: int, h: int, strength: float, warm: bool) -> tuple[Image.Image, Image.Image]:
    """The scrim's gradient mask and ground colour.

    Both depend only on the frame size and the look, none of which vary inside a
    render — but this was rebuilt with a pure-Python per-row loop on every one of
    a few hundred frames. Neither returned image is ever mutated by scrim(); it
    only reads them through Image.composite.
    """
    grad = Image.new("L", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / (h - 1)
        edge = max(0.0, 1.0 - abs(t - 0.5) * 2.0)  # 1 at centre, 0 at edges
        px[0, y] = int(255 * (1.0 - strength * (1.0 - edge * 0.55)))
    base = Image.new("RGB", (w, h), (26, 20, 30) if warm else (10, 12, 18))
    return grad.resize((w, h)), base


def _generic_disc(seed: str, size: int, mood: Mood) -> Image.Image:
    """The disc drawn when there is deliberately no likeness — --generic.

    Keyed to the person without depicting them: the hue comes from their DID, so
    the same author gets the same colour every time, and no pixel of their
    picture is ever fetched, let alone drawn. That is the whole guarantee, and
    tests/test_attribution.py holds it against the finished frame rather than
    against this function.

    Seeded on the DID and not the handle because handles change and DIDs do not.
    """
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    r, g, b = colorsys.hsv_to_rgb(digest[0] / 255.0, 0.38, 0.66)
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(disc).ellipse(
        [0, 0, size - 1, size - 1],
        fill=(int(r * 255), int(g * 255), int(b * 255), 255),
        outline=(*mood.rule, 210),
        width=3,
    )
    return disc


def _identity_disc(quote: Attribution, size: int, mood: Mood) -> Image.Image:
    """The author's picture, or a stand-in that is explicitly not their picture.

    `quote.avatar is None` only ever means --generic — make_video refuses a
    missing avatar with its own exit code rather than silently falling through
    to this branch, so reaching the generic disc is always a decision and never
    a failure that went unnoticed.
    """
    if quote.avatar is None:
        return _generic_disc(quote.author.did or quote.author.handle, size, mood)
    return circle_avatar(quote.avatar, size, ring=mood.rule)


def scrim(frame: Image.Image, *, strength: float = 0.44, warm: bool = True) -> Image.Image:
    """Darken footage so type survives on it, without flattening it to a slab.

    A vertical falloff — heavier top and bottom, lighter through the middle —
    keeps some of the image alive where nothing is written.

    Archival stock is usually dark and low-contrast to begin with, so the frame
    is lifted *before* the scrim goes on. Scrimming an already-muddy plate just
    produces mud, and then the moving image stops doing the one job it has.
    """
    frame = ImageOps.autocontrast(frame.convert("RGB"), cutoff=(1, 6))
    frame = ImageEnhance.Brightness(frame).enhance(1.12)
    w, h = frame.size
    grad, base = _falloff(w, h, strength, warm)
    return Image.composite(frame, base, grad)


@functools.lru_cache(maxsize=16)
def _halo(
    lines: tuple[str, ...],
    font_path: str,
    font_size: int,
    line_h: int,
    top: int,
    w: int,
    h: int,
    ink_shadow: tuple[int, int, int],
    align_centre: bool,
) -> Image.Image:
    """The blurred dark halo behind the type.

    Keyed on the lines rather than the frame: `reveal` only changes how many
    lines are shown, so a 240-frame render has 3-10 distinct halos and used to
    pay a full-frame Gaussian blur — the single most expensive thing in compose()
    — for every one of them. Never mutated by the caller, which only composites
    it.
    """
    font = load_font(Path(font_path).name, font_size)
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    y = top
    for line in lines:
        lw = hd.textlength(line, font=font)
        x = (w - lw) / 2 if align_centre else SAFE[0]
        hd.text((x, y), line, font=font, fill=(*ink_shadow, 210))
        y += line_h
    return halo.filter(ImageFilter.GaussianBlur(18))


def glow_text(
    img: Image.Image,
    lines: list[str],
    font,
    line_h: int,
    top: int,
    mood: Mood,
    *,
    align_centre: bool = True,
) -> None:
    """Draw type over footage so it stays legible on any frame.

    Two devices, both necessary: a blurred dark halo behind the glyphs handles
    bright patches, and a thin stroke handles high-frequency detail that a soft
    halo alone cannot separate from.
    """
    w, h = img.size
    img.alpha_composite(
        _halo(tuple(lines), font.path, font.size, line_h, top, w, h, mood.ink_shadow, align_centre)
    )

    d = ImageDraw.Draw(img)
    y = top
    for line in lines:
        lw = d.textlength(line, font=font)
        x = (w - lw) / 2 if align_centre else SAFE[0]
        d.text(
            (x, y),
            line,
            font=font,
            fill=mood.ink,
            stroke_width=max(1, font.size // 34),
            stroke_fill=(*mood.ink_shadow, 190),
        )
        y += line_h


def compose(
    plate: Image.Image,
    quote: Attribution,
    credit: tuple[str, str],
    *,
    mood: Mood = Mood(),
    reveal: float = 1.0,
) -> Image.Image:
    """One finished frame: scrimmed footage, type, author, credit.

    `reveal` is the fraction of lines shown, so the caller can pace the text in
    rather than dumping it — the wait is the retention mechanic the whole format
    borrows from the nodding-head trick. With the creature parked it is also the
    only thing in the frame that changes, apart from the footage itself.

    There is no avatar parameter. The picture arrives inside `quote`, because a
    face passed beside a name is the same hazard as text passed beside a handle,
    one layer down.
    """
    img = scrim(plate).convert("RGBA")
    d = ImageDraw.Draw(img)

    disc_top = SAFE[3] - PFP
    text_bottom = disc_top - 44
    box = (SAFE[2] - SAFE[0], text_bottom - SAFE[1] - 40)
    font, lines, line_h = fit_text(
        quote.text, "EBGaramond-SemiBold.ttf", box, d, lo=46, hi=118, leading=1.2
    )

    shown = max(1, round(len(lines) * max(0.0, min(1.0, reveal))))
    block_h = line_h * len(lines)
    top = SAFE[1] + max(0, (text_bottom - SAFE[1] - block_h) // 2)

    glow_text(img, lines[:shown], font, line_h, top, mood)

    # The author, bottom-left: their picture and their name, together, at the
    # corner of the safe area. This replaced a centred hairline rule and handle,
    # which was centred only to keep clear of the creature's antenna and had
    # nothing left to dodge.
    img.alpha_composite(_identity_disc(quote, PFP, mood), (SAFE[0], disc_top))

    nf = load_font("Inter-SemiBold.ttf", 34)
    ix = SAFE[0] + PFP + PFP_GAP

    # The handle is the attribution, so it is not the line that gives — the same
    # argument the rights line gets. A fixed size cannot cover the range:
    # "@a.bsky.social" is 198px at 28pt and "@averylongishhandle.bsky.social" is
    # 440px, against a 366px column. So it shrinks to fit rather than being cut,
    # and only a genuinely absurd handle reaches the floor and gets ellipsized.
    # The display name is what truncates, which is the right order — a shortened
    # name is cosmetic, a shortened handle points at the wrong account.
    handle = f"@{quote.author.handle}"
    for pt in range(28, 19, -2):
        hf = load_font("Inter-Regular.ttf", pt)
        if d.textlength(handle, font=hf) <= IDENT_COL:
            break

    # An account with no display name is reported by the AppView with its handle
    # *as* the display name, so drawing both prints the same string twice, one
    # line above the other, looking like a bug in the renderer. Anyone who never
    # set a display name gets this, which is a lot of people.
    display = (quote.author.display_name or "").strip()
    stacked = display and display.lstrip("@") != quote.author.handle

    # Secondary type needs the same halo as the body. Without it the handle
    # survives on dark plates and disappears on bright ones, which is the worst
    # of both — it looks like a rendering fault rather than a choice.
    shadow = {"stroke_width": 2, "stroke_fill": (*mood.ink_shadow, 210)}
    if stacked:
        iy = disc_top + (PFP - 78) // 2
        d.text((ix, iy), ellipsize(display, nf, IDENT_COL, d),
               font=nf, fill=mood.ink, **shadow)
        d.text((ix, iy + 44), ellipsize(handle, hf, IDENT_COL, d),
               font=hf, fill=mood.credit, **shadow)
    else:
        # One line, optically centred on the disc rather than on its box.
        # No display name: the handle carries the line alone, so it gets the
        # heavier face at whatever size it fitted at.
        solo = load_font("Inter-SemiBold.ttf", hf.size)
        d.text((ix, disc_top + (PFP - hf.size) // 2 - 6),
               ellipsize(handle, solo, IDENT_COL, d),
               font=solo, fill=mood.ink, **shadow)

    # Credit, right-aligned and bottom-aligned to the safe area. It used to sit
    # centred at CH-46 — 314px BELOW the safe area, directly under the Shorts
    # progress bar, so the one line naming the source and asserting public
    # domain was invisible on the surface we launch on.
    #
    # It cannot simply move up as a centred line: at 24px the median credit is
    # 711px wide and the widest 1186px. Hence two lines, right-aligned, smaller.
    cf = load_font("Inter-Regular.ttf", CREDIT_PT)
    lines = list(credit)
    # The rights statement is the line making a legal claim, so it is never
    # ellipsized — CREDIT_COL is reserved wide enough to hold it outright. A
    # long archival title is the line that gives.
    lines[0] = ellipsize(lines[0], cf, CREDIT_COL, d)
    line_h = int(CREDIT_PT * 1.28)
    y = SAFE[3] - line_h * len(lines)
    for line in lines:
        lw = d.textlength(line, font=cf)
        d.text(
            (SAFE[2] - lw, y), line, font=cf, fill=(*mood.credit, 235),
            stroke_width=2, stroke_fill=(*mood.ink_shadow, 210),
        )
        y += line_h
    return img.convert("RGB")
