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

from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

from skeet_frame import CH, CW, SAFE, Author, ellipsize, fit_text, load_font


@dataclass(frozen=True)
class Mood:
    """Colours for the text layer. Deliberately warm and light: the footage is
    century-old monochrome, and a cool overlay makes it read as dead rather
    than dreamy."""

    ink: tuple[int, int, int] = (255, 251, 244)
    ink_shadow: tuple[int, int, int] = (18, 14, 22)
    rule: tuple[int, int, int] = (236, 214, 176)
    credit: tuple[int, int, int] = (214, 206, 196)


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
    grad = Image.new("L", (1, h))
    px = grad.load()
    for y in range(h):
        t = y / (h - 1)
        edge = max(0.0, 1.0 - abs(t - 0.5) * 2.0)  # 1 at centre, 0 at edges
        px[0, y] = int(255 * (1.0 - strength * (1.0 - edge * 0.55)))
    grad = grad.resize((w, h))

    base = Image.new("RGB", (w, h), (26, 20, 30) if warm else (10, 12, 18))
    return Image.composite(frame, base, grad)


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
    halo = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    y = top
    for line in lines:
        lw = hd.textlength(line, font=font)
        x = (w - lw) / 2 if align_centre else SAFE[0]
        hd.text((x, y), line, font=font, fill=(*mood.ink_shadow, 210))
        y += line_h
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(18)))

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
    text: str,
    author: Author,
    credit: str,
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

    # Lift the figure clear of the bottom edge so its feet and contact shadow
    # have ground to stand on, and so it does not sit on the credit line.
    FLOOR = 58
    fig_h = figure.size[1] + FLOOR
    text_bottom = CH - fig_h - 40
    box = (SAFE[2] - SAFE[0], text_bottom - SAFE[1] - 120)
    font, lines, line_h = fit_text(text, "EBGaramond-SemiBold.ttf", box, d, lo=46, hi=118, leading=1.2)

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
        handle = ellipsize(f"@{author.handle}", hf, SAFE[2] - SAFE[0], d)
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
    # Off-centre. Dead-centre reads as a logo rather than a character, and it
    # aims the antenna straight up through the byline. Sitting left also gives
    # the pointing arm somewhere to point — up and inward, at the text.
    fx = int(CW * 0.30) - figure.size[0] // 2
    fy = CH - FLOOR - figure.size[1]
    sh = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse(
        [fx + figure.size[0] * 0.22, CH - FLOOR - 26,
         fx + figure.size[0] * 0.78, CH - FLOOR + 20],
        fill=(0, 0, 0, 125),
    )
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)))
    img.alpha_composite(figure, (fx, fy))

    cf = load_font("Inter-Regular.ttf", 24)
    cw_ = d.textlength(credit, font=cf)
    d.text(
        ((CW - cw_) / 2, CH - 46), credit, font=cf, fill=(*mood.credit, 190),
        stroke_width=2, stroke_fill=(*mood.ink_shadow, 190),
    )
    return img.convert("RGB")
