# pattern: Imperative Shell
"""Render one Bluesky post to a 9:16 short over public-domain b-roll.

    python3 render/make_video.py \
        --post https://bsky.app/profile/someone.bsky.social/post/3kxyz \
        --clip Designfo1956 --start 118 --out /tmp/out.mp4

The post is addressed by URL or at:// URI and never by handle-plus-text. An
earlier version took those separately and promptly put one person's words on
screen under another person's name — in a project about asking permission
first. Text, author, and avatar now arrive together or not at all.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image

import broll
import pair
import post as P
from figure import Pose, draw_figure, skin_from_pfp
from looks import compose
from skeet_frame import CH, CW, Author, load_font, unsupported_chars

FIG = (580, 850)

# Two idle cycles on deliberately unrelated periods. A single cycle reads as a
# mechanism; two that drift against each other read as something alive.
NOD_PERIOD = 1.70
BREATHE_PERIOD = 2.55
CYCLE = 5.10          # lowest common period of the two above
POSE_STEPS = 40       # poses cached across one cycle, then reused
BLINK_AT = (0.22, 0.68)   # fractions of the cycle where a blink lands
BLINK_LEN = 0.05


def build_poses(pfp, variant: str, point: bool, skin, generic: bool = False) -> list:
    """Pre-render one cycle of poses. Redrawing the figure per frame would
    triple render time for motion that repeats anyway."""
    out = []
    for i in range(POSE_STEPS):
        t = i / POSE_STEPS
        nod = math.sin(2 * math.pi * t * CYCLE / NOD_PERIOD)
        # Antenna follows the *derivative* of the nod, so it trails the head and
        # overshoots when the head stops. This is the whole liveness trick.
        nod_vel = math.cos(2 * math.pi * t * CYCLE / NOD_PERIOD)
        out.append(
            draw_figure(
                pfp, FIG, variant=variant, skin=skin, generic=generic,
                pose=Pose(
                    nod=nod,
                    breathe=math.sin(2 * math.pi * t * CYCLE / BREATHE_PERIOD),
                    antenna=-nod_vel * 21.0,
                    lean=nod * 1.1,
                    point=138 if point else None,
                    blink=any(b <= t < b + BLINK_LEN for b in BLINK_AT),
                ),
            )
        )

    # Variants leave different amounts of empty margin in the layer, so crop to
    # actual content. Union across every pose, not per-pose — a bbox that moves
    # frame to frame would make the creature jitter against the footage.
    boxes = [p.getbbox() for p in out if p.getbbox()]
    if boxes:
        union = (
            min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes),
        )
        out = [p.crop(union) for p in out]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", required=True, help="bsky.app URL or at:// URI")
    ap.add_argument("--clip", help="archive.org identifier; omitted means draw one from the library")
    ap.add_argument("--start", type=float, help="in-point in seconds; defaults to the drawn one")
    ap.add_argument("--dur", type=float, default=10.0)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--variant", default="face", choices=["face", "belly", "crab"])
    ap.add_argument("--point", action="store_true")
    ap.add_argument("--silent", action="store_true", help="drop the archival audio bed")
    ap.add_argument("--generic", action="store_true",
                    help="palette-only creature, no pfp — for asking before any likeness is used")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    sk = P.fetch(args.post)
    pfp = P.avatar(sk)
    if pfp is None:
        # No avatar set. --generic never shows one, so it proceeds on a
        # handle-derived palette; every other variant is a picture of their
        # picture and has nothing to draw.
        if not args.generic:
            print(f"@{sk.handle} has no avatar set — use --generic", file=sys.stderr)
            return 2
        pfp = Image.new("RGB", (256, 256), (128, 128, 128))
    author = Author(sk.display_name, sk.handle, None)
    print(f"post: @{sk.handle} · {sk.likes} likes · stands_alone={sk.stands_alone}")
    print(f"text: {sk.text!r}")

    if not sk.text.strip():
        print("post has no text (image-only?) — nothing to render", file=sys.stderr)
        return 2
    missing = unsupported_chars(sk.text, load_font("EBGaramond-SemiBold.ttf", 64))
    if missing:
        # The bundled faces are Latin-only and PIL does no fallback, so these
        # would draw as empty boxes under the author's real name. Refuse.
        shown = " ".join(sorted(missing)[:12])
        print(
            f"the bundled font has no glyphs for: {shown}\n"
            "rendering would show empty boxes where their words are — refusing.\n"
            "bundle a Noto fallback covering this script to support it.",
            file=sys.stderr,
        )
        return 3

    skin = skin_from_pfp(pfp, seed=sk.handle)

    if args.clip:
        clip = broll.fetch(args.clip)
        start = args.start if args.start is not None else 60.0
        source = "specified"
    else:
        pick = pair.choose(sk.uri, pair.load())
        clip = broll.fetch(pick.identifier)
        start = args.start if args.start is not None else pick.start
        source = "drawn"

    # Clamp the in-point so the whole window fits inside the film. The library
    # stores a good start time but not a duration, and jitter can push the
    # window past the end — which silently yields a short video rather than an
    # error, so it has to be caught here.
    clip_secs = broll.duration(clip.path)
    start = max(0.0, min(start, max(0.0, clip_secs - args.dur - 1.0)))
    print(f"clip ({source}): {clip.identifier} · {clip_secs:.0f}s · {clip.title[:44]}")
    print(f"in-point: {start:.1f}s")

    poses = build_poses(pfp, args.variant, args.point, skin, generic=args.generic)
    print(f"cached {len(poses)} poses over a {CYCLE:.1f}s cycle"
          f"{' (generic — no likeness used)' if args.generic else ''}")

    total = int(args.dur * args.fps)

    def gen():
        src = broll.frames(clip.path, (CW, CH), start=start, dur=args.dur + 0.5, fps=args.fps)
        for n, plate in enumerate(src):
            if n >= total:
                break
            t = n / args.fps
            idx = int((t % CYCLE) / CYCLE * POSE_STEPS) % POSE_STEPS
            reveal = min(1.0, 0.34 + (t / (args.dur * 0.45)) * 0.66)
            yield compose(plate, poses[idx], sk.text, author, clip.credit, reveal=reveal)
            if n % 48 == 0:
                print(f"  frame {n}/{total}", flush=True)

    out = Path(args.out)
    bed = None
    if not args.silent:
        bed = broll.audio_segment(clip.path, out.with_suffix(".bed.m4a"), start=start, dur=args.dur)
        print(f"audio bed: {'clip soundtrack' if bed else 'none (source silent)'}")
    broll.encode(gen(), out, (CW, CH), args.fps, audio=bed)
    if bed:
        bed.unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
