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

import cadence
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


def _line_x(drawn: str, anchor: str, font, w: int, align_centre: bool) -> float:
    """Where a line starts.

    Centred on the width of the line *when complete*, not on what is drawn so
    far. Words arrive one at a time, so centring each partial line on itself
    would slide every word already on screen leftward as the next one landed —
    the type would crawl rather than appear.
    """
    if not align_centre:
        return SAFE[0]
    d = ImageDraw.Draw(Image.new("L", (1, 1)))
    return (w - d.textlength(anchor or drawn, font=font)) / 2


@functools.lru_cache(maxsize=64)
def _halo(
    lines: tuple[str, ...],
    anchors: tuple[str, ...],
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

    Keyed on the lines rather than the frame, because a full-frame Gaussian blur
    is the single most expensive thing in compose() and the reveal changes the
    text far less often than it changes the frame. Word-paced reveal makes that
    caching matter more, not less: a 192-frame render now has one distinct halo
    per word — a few dozen — where it had one per line. Cutting only at word
    boundaries is what keeps that bounded; a per-character reveal would rebuild
    the blur on nearly every frame. Never mutated by the caller, which only
    composites it.
    """
    font = load_font(Path(font_path).name, font_size)
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    y = top
    for i, line in enumerate(lines):
        anchor = anchors[i] if i < len(anchors) else line
        hd.text((_line_x(line, anchor, font, w, align_centre), y),
                line, font=font, fill=(*ink_shadow, 210))
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
    anchors: tuple[str, ...] | None = None,
) -> None:
    """Draw type over footage so it stays legible on any frame.

    Two devices, both necessary: a blurred dark halo behind the glyphs handles
    bright patches, and a thin stroke handles high-frequency detail that a soft
    halo alone cannot separate from.

    `anchors` are the same lines complete, used only for horizontal placement so
    a partially-revealed line sits where it will finish.
    """
    w, h = img.size
    anchors = tuple(anchors) if anchors else tuple(lines)
    img.alpha_composite(
        _halo(tuple(lines), anchors, font.path, font.size, line_h, top, w, h,
              mood.ink_shadow, align_centre)
    )

    d = ImageDraw.Draw(img)
    y = top
    for i, line in enumerate(lines):
        anchor = anchors[i] if i < len(anchors) else line
        d.text(
            (_line_x(line, anchor, font, w, align_centre), y),
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

    `reveal` is how far through the reveal window the frame sits, 0 to 1. What
    that shows is decided by cadence.py, which paces the words the way someone
    reading aloud would — a beat at a comma, longer at a full stop. It used to be
    a fraction of *lines*, which paused wherever the column width happened to
    break: on a real post that meant pausing after "how", after "how to", and
    after "I", while every actual phrase ending sat mid-line and got nothing.

    The wait is the retention mechanic the whole format borrows from the
    nodding-head trick. With the creature parked it is also the only thing in the
    frame that changes, apart from the footage itself, which is why it is worth
    getting right.

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

    # The block is positioned from the *complete* text, so nothing moves as the
    # reveal proceeds — words appear into the layout the finished frame will have
    # rather than the type drifting upward as lines arrive.
    block_h = line_h * len(lines)
    top = SAFE[1] + max(0, (text_bottom - SAFE[1] - block_h) // 2)

    shown = cadence.visible(tuple(lines), max(0.0, min(1.0, reveal)))
    glow_text(img, list(shown), font, line_h, top, mood, anchors=tuple(lines))

    # The author, bottom-left: their picture and their name, together, at the
    # corner of the safe area. This replaced a centred hairline rule and handle,
    # which was centred only to keep clear of the creature's antenna and had
    # nothing left to dodge.
    img.alpha_composite(_identity_disc(quote, PFP, mood), (SAFE[0], disc_top))

    # Credit first, because it is the fixed obstacle and the identity block has
    # to be laid out around what it *actually* occupies.
    #
    # It used to sit centred at CH-46 — 314px BELOW the safe area, directly under
    # the Shorts progress bar, so the one line naming the source and asserting
    # public domain was invisible on the surface we launch on. It cannot simply
    # move up as a centred line either: at 24px the median credit is 711px wide.
    # Hence two lines, right-aligned, smaller.
    cf = load_font("Inter-Regular.ttf", CREDIT_PT)
    credit_lines = list(credit)
    # The rights statement makes a legal claim, so it is never ellipsized —
    # CREDIT_COL is wide enough to hold it outright. A long archival title is the
    # line that gives.
    credit_lines[0] = ellipsize(credit_lines[0], cf, CREDIT_COL, d)
    credit_lh = int(CREDIT_PT * 1.28)
    credit_top = SAFE[3] - credit_lh * len(credit_lines)
    credit_w = max(d.textlength(ln, font=cf) for ln in credit_lines)

    ix = SAFE[0] + PFP + PFP_GAP
    full_w = SAFE[2] - ix                                   # nothing in the way
    clear_w = int(SAFE[2] - credit_w - CREDIT_GUTTER - ix)   # beside the credit

    def budget(top: float, height: float) -> int:
        """How wide a line at this height may be.

        The credit is bottom-anchored and only two lines tall, so it occupies the
        floor of this strip and not the whole of it. Reserving its column against
        *every* identity line — which is what a single IDENT_COL constant does —
        charged the display name for an obstacle sitting well below it, and cut
        names to 366px when 770 were free. Measured per line instead: there is no
        virtue in two videos laying out identically, only in each one looking
        right.
        """
        return full_w if top + height <= credit_top else clear_w

    nf = load_font("Inter-SemiBold.ttf", 34)

    # An account with no display name is reported by the AppView with its handle
    # *as* the display name, so drawing both prints the same string twice, one
    # line above the other, looking like a bug in the renderer.
    display = (quote.author.display_name or "").strip()
    stacked = bool(display) and display.lstrip("@") != quote.author.handle
    handle = f"@{quote.author.handle}"

    # Secondary type needs the same halo as the body. Without it the handle
    # survives on dark plates and disappears on bright ones, which is the worst
    # of both — it looks like a rendering fault rather than a choice.
    shadow = {"stroke_width": 2, "stroke_fill": (*mood.ink_shadow, 210)}

    def place(height: int) -> int:
        """Top edge for an identity block of this height.

        Centred in the band *above* the credit when it fits there, rather than
        on the disc. Centring on the disc put the handle at the credit's own
        height: they never overlapped — the width budget saw to that — but they
        sat 24px apart on one line and read as a single crowded row rather than
        two separate things. Lifting the block clears the credit outright, which
        also hands both lines the full width instead of the column beside it.

        Falls back to centring on the disc when the credit is tall enough that
        there is no band to sit in, at which point the width budget takes over
        again and the layout degrades to the previous, still-correct, behaviour.
        """
        band = credit_top - disc_top
        if height + 12 <= band:
            return disc_top + (band - height) // 2
        return disc_top + (PFP - height) // 2

    if stacked:
        iy = place(78)
        hw = budget(iy + 44, 28)
        # The handle is the attribution and never gets cut — the same argument
        # the rights line gets — so it shrinks to whatever width it has. The
        # display name is the line allowed to ellipsize: a shortened name is
        # cosmetic, a shortened handle points at an account that is not theirs.
        for pt in range(28, 19, -2):
            hf = load_font("Inter-Regular.ttf", pt)
            if d.textlength(handle, font=hf) <= hw:
                break
        d.text((ix, iy), ellipsize(display, nf, budget(iy, 34), d),
               font=nf, fill=mood.ink, **shadow)
        d.text((ix, iy + 44), ellipsize(handle, hf, hw, d),
               font=hf, fill=mood.credit, **shadow)
    else:
        # Alone it carries the block, so it takes the heavier face.
        iy = place(34)
        sw = budget(iy, 34)
        for pt in range(34, 21, -2):
            sf = load_font("Inter-SemiBold.ttf", pt)
            if d.textlength(handle, font=sf) <= sw:
                break
        d.text((ix, iy), ellipsize(handle, sf, sw, d), font=sf, fill=mood.ink, **shadow)

    y = credit_top
    for line in credit_lines:
        lw = d.textlength(line, font=cf)
        d.text(
            (SAFE[2] - lw, y), line, font=cf, fill=(*mood.credit, 235),
            stroke_width=2, stroke_fill=(*mood.ink_shadow, 210),
        )
        y += credit_lh
    return img.convert("RGB")
