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

import functools
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from skeet_frame import CH, CW, SAFE, Attribution, ellipsize, fit_text, load_font


@dataclass(frozen=True)
class Mood:
    """Colours for the text layer. Deliberately warm and light: the footage is
    century-old monochrome, and a cool overlay makes it read as dead rather
    than dreamy."""

    ink: tuple[int, int, int] = (255, 251, 244)
    ink_shadow: tuple[int, int, int] = (18, 14, 22)
    rule: tuple[int, int, int] = (236, 214, 176)
    credit: tuple[int, int, int] = (214, 206, 196)


# How far the creature's feet sit above the bottom edge, so it has ground to
# stand on rather than being cut off by the frame. Module-level because the
# bounds tests need to reproduce the figure's placement exactly; a copy of the
# number in the test would pass while the render drifted away from it.
FLOOR = 58

# Where the creature is centred horizontally, as a fraction of the width. Off
# centre: dead-centre reads as a logo rather than a character, it aims the
# antenna straight up through the byline, and sitting left gives the pointing
# arm somewhere to point — up and inward, at the text.
FIG_CX = 0.30

# Credit type size, and the gutter between the creature's bounding box and the
# credit column. 20pt is not a taste call: the widest the creature gets is 646px
# and the safe area ends at 1016, so the column is 346px at worst, and
# "Prelinger Archives · public domain" measures 322px at 20pt and 354px at 22pt.
# Raising this silently pushes the rights line out of the safe area again.
CREDIT_PT = 20
CREDIT_GUTTER = 24


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


@functools.lru_cache(maxsize=4)
def _contact_shadow(fig_w: int, floor: int, w: int, h: int) -> Image.Image:
    """The creature's contact shadow.

    Depends only on the figure's width, which build_poses deliberately holds
    constant across every cached pose so the creature does not jitter — so this
    whole 1080x1920 blur was identical on every frame.
    """
    fx = int(w * FIG_CX) - fig_w // 2
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse(
        [fx + fig_w * 0.22, h - floor - 26, fx + fig_w * 0.78, h - floor + 20],
        fill=(0, 0, 0, 125),
    )
    return sh.filter(ImageFilter.GaussianBlur(20))


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
    figure: Image.Image,
    quote: Attribution,
    credit: tuple[str, str],
    *,
    mood: Mood = Mood(),
    reveal: float = 1.0,
) -> Image.Image:
    """One finished frame: scrimmed footage, type, creature, credit.

    `reveal` is the fraction of lines shown, so the caller can pace the text in
    rather than dumping it — the wait is the retention mechanic the whole format
    borrows from the nodding-head trick.
    """
    img = scrim(plate).convert("RGBA")
    d = ImageDraw.Draw(img)

    fig_h = figure.size[1] + FLOOR
    text_bottom = CH - fig_h - 40
    box = (SAFE[2] - SAFE[0], text_bottom - SAFE[1] - 120)
    font, lines, line_h = fit_text(
        quote.text, "EBGaramond-SemiBold.ttf", box, d, lo=46, hi=118, leading=1.2
    )

    shown = max(1, round(len(lines) * max(0.0, min(1.0, reveal))))
    block_h = line_h * len(lines)
    top = SAFE[1] + max(0, (text_bottom - SAFE[1] - block_h) // 2)

    glow_text(img, lines[:shown], font, line_h, top, mood)

    # Hairline rule + handle, set like a title card rather than a social byline.
    # Clamp the byline clear of the figure zone. Letting it float directly under
    # the text block puts it wherever the block happens to end, which is how the
    # antenna ended up spearing it.
    rule_y = min(top + block_h + 34, text_bottom - 96)
    if rule_y > SAFE[1]:
        d.line([(CW * 0.5 - 90, rule_y), (CW * 0.5 + 90, rule_y)], fill=mood.rule, width=2)
        hf = load_font("Inter-SemiBold.ttf", 34)
        handle = ellipsize(f"@{quote.author.handle}", hf, SAFE[2] - SAFE[0], d)
        hw = d.textlength(handle, font=hf)
        # Secondary type needs the same halo as the body. Without it the handle
        # survives on dark plates and disappears on bright ones, which is the
        # worst of both — it looks like a rendering fault rather than a choice.
        d.text(
            ((CW - hw) / 2, rule_y + 22), handle, font=hf, fill=mood.credit,
            stroke_width=2, stroke_fill=(*mood.ink_shadow, 200),
        )

    # Contact shadow. Without it the creature reads as a sticker pasted on the
    # footage rather than something standing in the scene.
    fx = int(CW * FIG_CX) - figure.size[0] // 2
    fy = CH - FLOOR - figure.size[1]
    img.alpha_composite(_contact_shadow(figure.size[0], FLOOR, CW, CH))
    img.alpha_composite(figure, (fx, fy))

    # Credit, in the clear column beside the creature and bottom-aligned to the
    # safe area. It used to sit centred at CH-46 — 314px BELOW the safe area,
    # directly under the Shorts progress bar, so the one line naming the source
    # and asserting public domain was invisible on the surface we launch on.
    #
    # It cannot simply move up as a centred line: at 24px the median credit is
    # 711px wide and the widest 1186px, while the column left clear by the
    # creature is ~350px. Hence two lines, right-aligned, smaller. Drawn after
    # the figure so it wins any overlap rather than being half-covered.
    cf = load_font("Inter-Regular.ttf", CREDIT_PT)
    gap = SAFE[2] - (fx + figure.size[0]) - CREDIT_GUTTER
    lines = list(credit)
    # The rights statement is the line making a legal claim, so it is never
    # ellipsized — it is sized to fit the narrowest column the creature leaves.
    # A long archival title is the line that gives.
    lines[0] = ellipsize(lines[0], cf, gap, d)
    line_h = int(CREDIT_PT * 1.28)
    y = SAFE[3] - line_h * len(lines)
    for line in lines:
        lw = d.textlength(line, font=cf)
        d.text(
            (SAFE[2] - lw, y), line, font=cf, fill=(*mood.credit, 190),
            stroke_width=2, stroke_fill=(*mood.ink_shadow, 190),
        )
        y += line_h
    return img.convert("RGB")
