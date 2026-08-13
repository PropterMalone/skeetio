# pattern: Functional Core
"""A small posable creature that carries someone's profile picture.

The pfp goes either where its face should be, or on its belly like a screen.
The belly variant is the stranger of the two: the creature keeps its own eyes,
so it reads as something *presenting* your post rather than as your head on a
body.

Why a figure at all, rather than the floating avatar disc this project started
with: a disc can only translate, so a nod is a few pixels of vertical travel
and reads as a compression artifact. A body can bob, lean, squash and — the
pose the design doc asks for and a disc simply cannot make — point.

Everything is drawn at SS× and downsampled, because the whole look depends on
edges that are soft rather than aliased.
"""

from __future__ import annotations

import colorsys
import hashlib
import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFilter

SS = 3  # supersample factor

# Antenna bobble radius, as a fraction of figure width. Shared, because the
# headroom calculation and the drawing code must agree about it — the first
# version derived clearance from the stalk length alone and the ball, drawn
# centred on the tip, promptly hung over the top edge.
ANT_BALL = 0.038


@dataclass(frozen=True)
class Skin:
    """Creature colours. Kept separate from the page palette: the figure should
    stay recognisably itself across whatever ground it is placed on."""

    body: tuple[int, int, int] = (250, 243, 232)
    body_shade: tuple[int, int, int] = (214, 199, 189)
    body_light: tuple[int, int, int] = (255, 253, 250)
    limb: tuple[int, int, int] = (243, 232, 219)
    blush: tuple[int, int, int] = (243, 168, 158)
    ink: tuple[int, int, int] = (58, 48, 62)
    ring: tuple[int, int, int] = (255, 252, 247)


def skin_from_pfp(pfp: Image.Image, *, seed: str = "") -> Skin:
    """Derive the creature's palette from the profile picture it will carry.

    Two reasons this earns its place. It makes every creature bespoke to its
    author without anyone choosing colours, and — more usefully — a creature
    *keyed* to someone's pfp is personalised without being a likeness, which is
    a real middle tier between a generic mascot and an animated photograph.

    Monochrome avatars are common (line art, logos, old photos) and would all
    collapse to the same grey creature, so when saturation is too low to read a
    hue we derive one deterministically from the handle instead. Same author,
    same creature, every time; different authors still differ.
    """
    small = pfp.convert("RGB").resize((64, 64), Image.LANCZOS)
    quant = small.quantize(colors=8, method=Image.MEDIANCUT).convert("RGB")
    counts: dict[tuple[int, int, int], int] = {}
    for px in quant.getdata():
        counts[px] = counts.get(px, 0) + 1

    hsv = [
        (colorsys.rgb_to_hsv(r / 255, g / 255, b / 255), n)
        for (r, g, b), n in counts.items()
    ]
    sat_mass = sum(h[1] * n for h, n in hsv) / max(1, sum(n for _, n in hsv))

    if sat_mass < 0.14:
        # Effectively monochrome. Hash the handle to a hue so the creature is
        # still this author's creature rather than everyone's grey one.
        hue = (int(hashlib.sha1(seed.encode()).hexdigest()[:8], 16) % 360) / 360.0
        sat = 0.34
    else:
        # Weight by saturation *and* frequency: the most common colour in a
        # photo is usually background, and the most saturated single pixel is
        # usually noise. The product picks the colour the image is actually about.
        (h, s, _v), _ = max(hsv, key=lambda t: t[0][1] * (t[1] ** 0.5))
        hue, sat = h, max(0.28, min(0.62, s))

    def rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
        r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
        return (int(r * 255), int(g * 255), int(b * 255))

    return Skin(
        body=rgb(hue, sat * 0.16, 0.97),
        body_shade=rgb(hue, sat * 0.44, 0.80),
        body_light=rgb(hue, sat * 0.05, 1.0),
        limb=rgb(hue, sat * 0.26, 0.94),
        blush=rgb(hue, sat * 0.92, 0.93),
        ink=rgb(hue, 0.30, 0.22),
        ring=rgb(hue, 0.04, 1.0),
    )


@dataclass(frozen=True)
class Pose:
    """`nod` runs -1 (head down, body squashed) to 1 (head up, body stretched).
    `point` swings the right arm to an angle in degrees, measured from straight
    down; None leaves both arms resting. `lean` tips the whole figure.

    `breathe` is a small independent idle on a different period from the nod —
    two unsynchronised cycles read as alive where one reads as a mechanism.

    `antenna` is the lag angle in degrees, and it is the most valuable field
    here. Driving it from the *derivative* of the nod rather than the nod itself
    makes the antenna trail the head and overshoot when it stops, which is the
    follow-through that separates a character from a moving picture."""

    nod: float = 0.0
    point: float | None = None
    lean: float = 0.0
    blink: bool = False
    breathe: float = 0.0
    antenna: float = 0.0


def _capsule(
    draw: ImageDraw.ImageDraw,
    a: tuple[float, float],
    b: tuple[float, float],
    r0: float,
    r1: float,
    fill: tuple[int, int, int],
) -> None:
    """A tapered limb: two circles and the quad bridging their tangents."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / L, dx / L
    draw.polygon(
        [
            (ax + nx * r0, ay + ny * r0),
            (bx + nx * r1, by + ny * r1),
            (bx - nx * r1, by - ny * r1),
            (ax - nx * r0, ay - ny * r0),
        ],
        fill=fill,
    )
    draw.ellipse([ax - r0, ay - r0, ax + r0, ay + r0], fill=fill)
    draw.ellipse([bx - r1, by - r1, bx + r1, by + r1], fill=fill)


def _shade(layer: Image.Image, skin: Skin) -> Image.Image:
    """Give a flat silhouette volume: a soft form shadow low and right, a
    highlight high and left, both clipped to the shape."""
    alpha = layer.split()[3]
    w, h = layer.size

    shadow_mask = alpha.filter(ImageFilter.GaussianBlur(w // 26))
    shadow_mask = shadow_mask.transform(
        (w, h), Image.AFFINE, (1, 0, -w * 0.055, 0, 1, -h * 0.05), resample=Image.BICUBIC
    )
    shade = Image.new("RGBA", (w, h), (*skin.body_shade, 255))
    shade.putalpha(Image.eval(shadow_mask, lambda v: int(v * 0.55)))
    inv = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    inv.alpha_composite(shade)
    inv.putalpha(Image.composite(inv.split()[3], Image.new("L", (w, h), 0), alpha))

    light_mask = alpha.filter(ImageFilter.GaussianBlur(w // 30))
    light_mask = light_mask.transform(
        (w, h), Image.AFFINE, (1, 0, w * 0.05, 0, 1, h * 0.045), resample=Image.BICUBIC
    )
    lit = Image.new("RGBA", (w, h), (*skin.body_light, 255))
    lit.putalpha(Image.eval(light_mask, lambda v: int(v * 0.5)))
    lit.putalpha(Image.composite(lit.split()[3], Image.new("L", (w, h), 0), alpha))

    out = layer.copy()
    out.alpha_composite(inv)
    out.alpha_composite(lit)
    out.putalpha(alpha)
    return out


def _circle_fit(src: Image.Image, size: int) -> Image.Image:
    w, h = src.size
    side = min(w, h)
    im = (
        src.crop(((w - side) // 2, (h - side) // 2, (w - side) // 2 + side, (h - side) // 2 + side))
        .resize((size, size), Image.LANCZOS)
        .convert("RGBA")
    )
    m = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(m).ellipse([0, 0, size * 4 - 1, size * 4 - 1], fill=255)
    im.putalpha(m.resize((size, size), Image.LANCZOS))
    return im


def _eyes(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    r: float,
    skin: Skin,
    *,
    blink: bool,
) -> None:
    gap, ey = r * 0.42, cy
    for sx in (-1, 1):
        x = cx + sx * gap
        if blink:
            draw.line([(x - r * 0.16, ey), (x + r * 0.16, ey)], fill=skin.ink, width=int(r * 0.075))
        else:
            draw.ellipse(
                [x - r * 0.115, ey - r * 0.155, x + r * 0.115, ey + r * 0.155], fill=skin.ink
            )
            draw.ellipse(
                [
                    x - r * 0.04 + r * 0.035,
                    ey - r * 0.115,
                    x + r * 0.035 + r * 0.035,
                    ey - r * 0.04,
                ],
                fill=(255, 255, 255),
            )
    for sx in (-1, 1):
        x = cx + sx * (gap + r * 0.30)
        draw.ellipse(
            [x - r * 0.135, ey + r * 0.10, x + r * 0.135, ey + r * 0.235], fill=skin.blush
        )


def _antenna(
    d: ImageDraw.ImageDraw,
    cx: float,
    base_y: float,
    length: float,
    lag_deg: float,
    skin: Skin,
    W: int,
) -> None:
    """A stalk that bends by `lag_deg`, increasing toward the tip.

    Deflection is quadratic along the stalk rather than linear so it reads as a
    flexible whip rather than a hinged rod — the base stays planted and the tip
    does the travelling, which is what makes the overshoot legible.
    """
    steps = 12
    pts = []
    for i in range(steps + 1):
        t = i / steps
        ang = math.radians(lag_deg * t * t)
        pts.append((cx + math.sin(ang) * length * t, base_y - math.cos(ang) * length * t))
    d.line(pts, fill=skin.limb, width=int(W * 0.020), joint="curve")
    tip = pts[-1]
    r = W * ANT_BALL
    d.ellipse([tip[0] - r, tip[1] - r, tip[0] + r, tip[1] + r], fill=skin.blush)
    r2 = r * 0.34
    d.ellipse(
        [tip[0] - r2 - r * 0.22, tip[1] - r2 - r * 0.22, tip[0] + r2 - r * 0.22, tip[1] + r2 - r * 0.22],
        fill=skin.ring,
    )


def _plating(
    d: ImageDraw.ImageDraw,
    cx: float,
    body_top: float,
    body_bot: float,
    body_l: float,
    body_r: float,
    skin: Skin,
    W: int,
    *,
    lamp: bool,
    lamp_on: float = 1.0,
) -> None:
    """Seam and rivets. The figure reads as a robot, so give it the vocabulary of
    one rather than leaving it a generic soft blob."""
    # A curved seam plus two rivets reads as eyes-and-a-smile, giving the torso
    # an accidental second face that fights the actual head. Straight seam, and
    # the rivets sit *on* it at the outer edge, where they read as fixings.
    seam_y = body_top + (body_bot - body_top) * 0.20
    half = (body_r - body_l) * 0.30
    d.line(
        [(cx - half, seam_y), (cx + half, seam_y)],
        fill=skin.body_shade, width=max(2, int(W * 0.006)),
    )
    for sx in (-1, 1):
        rx = cx + sx * half
        rr = W * 0.012
        d.ellipse([rx - rr, seam_y - rr, rx + rr, seam_y + rr], fill=skin.body_shade)

    if lamp:
        ly = body_top + (body_bot - body_top) * 0.55
        lr = W * 0.036
        d.ellipse([cx - lr, ly - lr, cx + lr, ly + lr], fill=skin.body_shade)
        gr = lr * (0.42 + 0.34 * max(0.0, min(1.0, lamp_on)))
        d.ellipse([cx - gr, ly - gr, cx + gr, ly + gr], fill=skin.blush)


def _jointed_leg(
    d: ImageDraw.ImageDraw,
    origin: tuple[float, float],
    out_ang: float,
    length: float,
    knee_lift: float,
    skin: Skin,
    W: int,
) -> None:
    """A two-segment crab leg: out and up to a knee, then down to a tip.

    Crab legs bend *upward* at the joint and come back down, which is what makes
    the silhouette read as a crab rather than a spider. `knee_lift` raises that
    joint; animating it per-leg is what gives the scuttle.
    """
    ox, oy = origin
    a = math.radians(out_ang)
    knee = (ox + math.cos(a) * length * 0.55, oy - math.sin(a) * length * 0.55 - knee_lift)
    tip = (knee[0] + math.cos(a) * length * 0.62, knee[1] + length * 0.52)
    _capsule(d, (ox, oy), knee, W * 0.018, W * 0.013, skin.limb)
    _capsule(d, knee, tip, W * 0.013, W * 0.007, skin.limb)
    r = W * 0.010
    d.ellipse([knee[0] - r, knee[1] - r, knee[0] + r, knee[1] + r], fill=skin.limb)


def _claw(
    d: ImageDraw.ImageDraw,
    shoulder: tuple[float, float],
    ang: float,
    length: float,
    skin: Skin,
    W: int,
    *,
    open_frac: float = 0.30,
) -> None:
    """An arm ending in a pincer. The gap between the two halves is what makes
    it read as a claw rather than a mitten, so it is never drawn fully closed."""
    sx, sy = shoulder
    a = math.radians(ang)
    elbow = (sx + math.cos(a) * length * 0.5, sy - math.sin(a) * length * 0.5)
    _capsule(d, (sx, sy), elbow, W * 0.034, W * 0.026, skin.limb)

    hand = (elbow[0] + math.cos(a) * length * 0.34, elbow[1] - math.sin(a) * length * 0.34)
    cr = W * 0.062
    for sign, frac in ((1, open_frac), (-1, open_frac * 0.55)):
        spread = math.radians(sign * 26 * (0.4 + frac))
        tipx = hand[0] + math.cos(a + spread) * cr * 1.5
        tipy = hand[1] - math.sin(a + spread) * cr * 1.5
        _capsule(d, hand, (tipx, tipy), cr * 0.62, cr * 0.22, skin.limb)
    d.ellipse([hand[0] - cr * 0.66, hand[1] - cr * 0.66,
               hand[0] + cr * 0.66, hand[1] + cr * 0.66], fill=skin.limb)


def _eyestalk(
    d: ImageDraw.ImageDraw,
    base: tuple[float, float],
    lean: float,
    length: float,
    skin: Skin,
    W: int,
    *,
    blink: bool,
) -> None:
    """A stalked eye that bends by `lean`, quadratically toward the tip.

    This is the antenna trick with an animal's excuse for it: crabs really do
    carry their eyes on stalks, so the follow-through that made the robot feel
    alive is anatomy here rather than decoration.
    """
    bx, by = base
    pts = []
    for i in range(11):
        t = i / 10
        a = math.radians(lean * t * t)
        pts.append((bx + math.sin(a) * length * t, by - math.cos(a) * length * t))
    d.line(pts, fill=skin.limb, width=int(W * 0.022), joint="curve")
    ex, ey = pts[-1]
    r = W * 0.040
    d.ellipse([ex - r, ey - r, ex + r, ey + r], fill=skin.ring)
    if blink:
        d.line([(ex - r * 0.7, ey), (ex + r * 0.7, ey)], fill=skin.ink, width=int(W * 0.011))
    else:
        pr = r * 0.52
        d.ellipse([ex - pr, ey - pr, ex + pr, ey + pr], fill=skin.ink)
        gr = pr * 0.36
        d.ellipse([ex - gr + pr * 0.3, ey - gr - pr * 0.25,
                   ex + gr + pr * 0.3, ey + gr - pr * 0.25], fill=(255, 255, 255))


def draw_figure(
    pfp: Image.Image,
    size: tuple[int, int],
    *,
    variant: str = "belly",
    pose: Pose = Pose(),
    skin: Skin = Skin(),
    generic: bool = False,
) -> Image.Image:
    """Render the creature into an RGBA layer of `size`.

    Proportions are expressed against the layer box so a caller can scale the
    figure just by asking for a bigger one.

    `generic` renders the creature with no profile picture at all — only the
    palette derived from it. That is personalised without being a likeness, and
    it is what gets sent when nobody has granted permission for their face yet.
    The creature keeps eyes of its own in this mode regardless of variant, so it
    reads as a character rather than as a figure with its head missing.
    """
    # Horizontal margin, for the same reason the vertical headroom exists — and
    # it cannot be bought by asking for a bigger layer. Every coordinate here is
    # a fraction of W, so a raised arm reaches ~1.08W and the crab's legs ~1.03W
    # at ANY canvas size; scaling the canvas scales the overshoot with it. The
    # margin has to enter the coordinate system, so the figure is drawn against
    # its own W inside a wider canvas. Callers crop to the union bounding box,
    # so the slack costs nothing in the finished frame.
    W, H = size[0] * SS, size[1] * SS
    PAD = int(W * 0.16)
    CANVAS_W = W + PAD * 2
    layer = Image.new("RGBA", (CANVAS_W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0 = PAD  # left edge of the figure's own coordinate space

    nod = max(-1.0, min(1.0, pose.nod))

    if variant == "crab":
        shell_r = W * 0.290
        # Eyestalks reach highest; solve the top of the body from that rather
        # than guessing, same rule as the antenna.
        stalk = shell_r * 0.62
        cy = max(H * 0.42, H * 0.030 + stalk + shell_r * 1.02 + H * 0.030) + nod * H * 0.018
        cx = x0 + W * 0.5

        # Legs first so the shell overlaps their roots. Back pairs sit higher
        # and shorter, which gives the cluster depth without any real z-order.
        for side in (-1, 1):
            for i, (spread, ln, ph) in enumerate(
                ((-6, 0.72, 0.0), (-26, 0.80, 0.33), (-46, 0.70, 0.66))
            ):
                lift = math.sin(2 * math.pi * (nod * 0.5 + ph)) * W * 0.026
                # Roots sit on the shell edge, not inside it. Started at 0.72r
                # and the whole leg cluster hid behind the carapace, leaving
                # stubs — a crab silhouette is mostly legs, so they have to
                # clear the body by a good margin.
                _jointed_leg(
                    d,
                    (cx + side * shell_r * 0.88, cy + shell_r * (0.10 + i * 0.17)),
                    (0 if side > 0 else 180) - side * spread,
                    shell_r * ln,
                    lift,
                    skin,
                    W,
                )

        # Claws. The right one raises to point when asked; the left keeps a
        # small idle sway so the pair never looks frozen.
        for side in (-1, 1):
            base_ang = 52 if side > 0 else 128
            pointing = side > 0 and pose.point is not None
            if pointing:
                # Reach high and gape wide. At the idle angle and idle gape the
                # pointing pose was indistinguishable from resting.
                ang = 180 - pose.point
                reach, gape = 1.02, 0.62
            else:
                ang = base_ang + nod * 5 * side
                reach, gape = 0.86, 0.28 + 0.16 * (nod + 1) / 2
            _claw(d, (cx + side * shell_r * 0.80, cy - shell_r * 0.12), ang,
                  shell_r * reach, skin, W, open_frac=gape)

        # Carapace.
        d.ellipse([cx - shell_r * 1.06, cy - shell_r * 0.94,
                   cx + shell_r * 1.06, cy + shell_r * 0.98], fill=skin.body)
        layer = _shade(layer, skin)
        d = ImageDraw.Draw(layer)

        for side in (-1, 1):
            _eyestalk(d, (cx + side * shell_r * 0.34, cy - shell_r * 0.80),
                      pose.antenna + side * 5, stalk, skin, W, blink=pose.blink)

        inner = int(shell_r * 1.62)
        if generic:
            d.ellipse([cx - inner / 2, cy - inner / 2, cx + inner / 2, cy + inner / 2],
                      fill=skin.body_shade)
            d.arc([cx - inner * 0.26, cy - inner * 0.20, cx + inner * 0.26, cy + inner * 0.30],
                  start=200, end=340, fill=skin.body, width=max(2, int(W * 0.007)))
        else:
            d.ellipse([cx - inner / 2 - W * 0.014, cy - inner / 2 - W * 0.014,
                       cx + inner / 2 + W * 0.014, cy + inner / 2 + W * 0.014], fill=skin.ring)
            layer.alpha_composite(_circle_fit(pfp, inner),
                                  (int(cx - inner / 2), int(cy - inner / 2)))

        out_w = round(CANVAS_W / SS)
        layer = layer.resize((out_w, size[1]), Image.LANCZOS)
        if pose.lean:
            layer = layer.rotate(pose.lean, resample=Image.BICUBIC, expand=False)
        return layer

    # Squash-and-stretch: the body compresses as the head drops. Without this a
    # nod looks like a head sliding on a static torso.
    squash = 1.0 - nod * 0.045 + pose.breathe * 0.020
    head_dy = -nod * H * 0.030 - pose.breathe * H * 0.006

    head_r = W * (0.285 if variant == "face" else 0.175)
    neck = 0.66 if variant == "face" else 0.52          # head centre above body_top
    ant_base = 1.00 if variant == "face" else 0.92      # antenna root above head centre
    ant_len = 0.46 if variant == "face" else 0.78       # antenna length

    # Derive headroom rather than tuning a fraction by hand. The antenna tip
    # rides highest when the nod lifts the head (0.030H) and breathing adds to
    # it (0.006H); a hand-picked body_top clears that at one set of proportions
    # and silently starts cutting the tip off at the next. Solving for it means
    # changing the head size or antenna length cannot reintroduce the clip.
    lift = H * 0.036
    margin = H * 0.030
    body_top = max(
        H * 0.30,
        margin + head_r * (neck + ant_base + ant_len) + lift + W * ANT_BALL,
    )
    body_bot = H * 0.875
    body_h = (body_bot - body_top) * squash
    body_bot = body_top + body_h
    body_l, body_r = x0 + W * 0.175, x0 + W * 0.825
    cx = x0 + W * 0.5

    # Feet first, so the body overlaps them.
    foot_y = body_bot - H * 0.008
    for sx in (-1, 1):
        fx = cx + sx * W * 0.155
        d.ellipse(
            [fx - W * 0.088, foot_y - H * 0.030, fx + W * 0.088, foot_y + H * 0.052],
            fill=skin.limb,
        )

    # The face variant needs a much bigger head: the pfp *is* the face, so it
    # has to hold its own against the torso rather than perch on it.
    head_cy = body_top - head_r * neck + head_dy
    shoulder_y = body_top + body_h * 0.20

    # Arms. The pointing arm swings from straight-down toward the given angle.
    arm_r0, arm_r1 = W * 0.055, W * 0.040
    for sx in (-1, 1):
        sxp = cx + sx * (W * 0.315)
        if sx == 1 and pose.point is not None:
            # 0° is straight down, 180° straight up, so a value past 90 raises
            # the arm — which is where the text is.
            ang = math.radians(pose.point)
            L = W * 0.40
            end = (sxp + math.sin(ang) * L, shoulder_y + math.cos(ang) * L)
        else:
            swing = math.radians(16 + nod * 7)
            L = W * 0.235
            end = (sxp + sx * math.sin(swing) * L, shoulder_y + math.cos(swing) * L)
        _capsule(d, (sxp, shoulder_y), end, arm_r0, arm_r1, skin.limb)
        d.ellipse(
            [end[0] - arm_r1 * 1.32, end[1] - arm_r1 * 1.32, end[0] + arm_r1 * 1.32, end[1] + arm_r1 * 1.32],
            fill=skin.limb,
        )

    # Torso — a bean, wider at the hips.
    d.rounded_rectangle(
        [body_l, body_top, body_r, body_bot], radius=(body_r - body_l) * 0.46, fill=skin.body
    )
    d.ellipse(
        [body_l - W * 0.012, body_top + body_h * 0.30, body_r + W * 0.012, body_bot], fill=skin.body
    )

    if variant == "face":
        d.ellipse(
            [cx - head_r * 1.10, head_cy - head_r * 1.10, cx + head_r * 1.10, head_cy + head_r * 1.10],
            fill=skin.ring,
        )
    else:
        d.ellipse(
            [cx - head_r * 0.86, head_cy - head_r * 0.86, cx + head_r * 0.86, head_cy + head_r * 0.86],
            fill=skin.body,
        )

    layer = _shade(layer, skin)
    d = ImageDraw.Draw(layer)

    _plating(
        d, cx, body_top, body_bot, body_l, body_r, skin, W,
        lamp=(variant == "face"), lamp_on=(nod + 1) / 2,
    )

    # Antenna is drawn before the pfp on the face variant so the head overlaps
    # its base, and it belongs on both variants — it is the part that carries
    # the follow-through.
    _antenna(d, cx, head_cy - head_r * ant_base, head_r * ant_len, pose.antenna, skin, W)

    if variant == "face":
        r = int(head_r * 1.98)
        if generic:
            d.ellipse([cx - r / 2, head_cy - r / 2, cx + r / 2, head_cy + r / 2], fill=skin.body)
            _eyes(d, cx, head_cy + head_r * 0.10, head_r * 0.92, skin, blink=pose.blink)
        else:
            layer.alpha_composite(_circle_fit(pfp, r), (int(cx - r / 2), int(head_cy - r / 2)))
    else:
        _eyes(d, cx, head_cy + head_r * 0.06, head_r * 0.86, skin, blink=pose.blink)
        belly_r = int((body_r - body_l) * 0.62)
        belly_cy = body_top + body_h * 0.52
        d.ellipse(
            [
                cx - belly_r / 2 - W * 0.017,
                belly_cy - belly_r / 2 - W * 0.017,
                cx + belly_r / 2 + W * 0.017,
                belly_cy + belly_r / 2 + W * 0.017,
            ],
            fill=skin.ring,
        )
        if generic:
            d.ellipse(
                [cx - belly_r / 2, belly_cy - belly_r / 2, cx + belly_r / 2, belly_cy + belly_r / 2],
                fill=skin.body,
            )
            d.arc(
                [cx - belly_r * 0.30, belly_cy - belly_r * 0.30,
                 cx + belly_r * 0.30, belly_cy + belly_r * 0.30],
                start=200, end=340, fill=skin.body_shade, width=max(2, int(W * 0.006)),
            )
        else:
            layer.alpha_composite(
                _circle_fit(pfp, belly_r), (int(cx - belly_r / 2), int(belly_cy - belly_r / 2))
            )

    out_w = round(CANVAS_W / SS)
    layer = layer.resize((out_w, size[1]), Image.LANCZOS)
    if pose.lean:
        layer = layer.rotate(pose.lean, resample=Image.BICUBIC, expand=False)
    return layer
