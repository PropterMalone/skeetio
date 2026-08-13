"""The creature must not be cut off — by its own layer, or by the frame.

This exists because the bug it guards was fixed *silently*, in a commit about
something else, and the fix reached the private tree but not the public release.
Nothing in the repo could tell the two apart, because nothing in the repo
measured the creature at all.

Two separate claims, and passing the first does not imply the second:

  1. The figure does not cut its own RGBA layer. Every coordinate in figure.py
     is a fraction of W, so a raised arm reaches ~1.08W and the crab's legs
     ~1.03W at ANY layer size — asking for a bigger layer scales the overshoot
     with it and changes nothing. Only the internal PAD margin fixes this.
  2. The cropped figure, placed where compose() puts it, fits inside the 1080x1920
     frame. PIL raises on a negative composite offset, so failure here is a crash
     in the renderer rather than a cosmetic clip.

Not asserted: that the creature stays inside SAFE. It deliberately does not —
a character standing at the bottom of frame may be partly overlaid by the Shorts
chrome. Only *type* has to respect the safe area (see test_safe_area.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

import figure as figure_mod
from figure import draw_figure, skin_from_pfp
from looks import FIG_CX, FLOOR
from make_video import FIG, POSE_STEPS, build_poses, pose_at
from skeet_frame import CH, CW

VARIANTS = ("belly", "face", "crab")
POINTS = (False, True)

# Alpha below this is antialiasing fringe rather than content.
INK = 8


@pytest.fixture(scope="module")
def pfp() -> Image.Image:
    return Image.new("RGB", (400, 400), (120, 90, 200))


@pytest.fixture(scope="module")
def skin(pfp):
    return skin_from_pfp(pfp, seed="bounds-test")


@pytest.fixture(autouse=True)
def _no_supersample(monkeypatch):
    """Draw at SS=1 for the tests.

    Every proportion in figure.py is a fraction of W, and the layer is
    downsampled to CANVAS_W/SS at the end, so SS only changes edge quality — the
    geometry under test is identical and the render is ~9x cheaper. Keeping the
    full-quality path in the test would make it slow enough that nobody runs it,
    which is the failure mode that let this bug ship.
    """
    monkeypatch.setattr(figure_mod, "SS", 1)


def _edge_columns(im: Image.Image) -> tuple[int, int]:
    """Max alpha found on the layer's first and last column."""
    a = np.array(im.split()[3])
    return int(a[:, 0].max()), int(a[:, -1].max())


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("point", POINTS)
def test_figure_does_not_cut_its_own_layer(pfp, skin, variant, point):
    """Non-zero alpha on a boundary column means the shape was sliced flat."""
    worst_left = worst_right = 0
    for i in range(POSE_STEPS):
        im = draw_figure(
            pfp, FIG, variant=variant, skin=skin, pose=pose_at(i / POSE_STEPS, point)
        )
        left, right = _edge_columns(im)
        worst_left = max(worst_left, left)
        worst_right = max(worst_right, right)

    assert worst_left <= INK, (
        f"{variant} point={point}: creature is cut flat against the LEFT edge of "
        f"its own layer (alpha {worst_left}). Widen PAD in figure.draw_figure — "
        f"a bigger `size` will not help, the overshoot scales with it."
    )
    assert worst_right <= INK, (
        f"{variant} point={point}: creature is cut flat against the RIGHT edge of "
        f"its own layer (alpha {worst_right}). Widen PAD in figure.draw_figure — "
        f"a bigger `size` will not help, the overshoot scales with it."
    )


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("point", POINTS)
def test_placed_figure_fits_the_frame(pfp, skin, variant, point):
    """compose() centres the cropped figure on CW*FIG_CX and drops it on the
    floor. A wide pose pushes the left edge past 0, where PIL raises."""
    poses = build_poses(pfp, variant, point, skin)
    w, h = poses[0].size
    assert all(p.size == (w, h) for p in poses), "poses must share one bbox or the creature jitters"

    fx = int(CW * FIG_CX) - w // 2
    fy = CH - FLOOR - h

    assert fx >= 0, f"{variant} point={point}: figure starts at x={fx}; PIL raises on a negative offset"
    assert fx + w <= CW, f"{variant} point={point}: figure runs {fx + w - CW}px past the right edge"
    assert fy >= 0, f"{variant} point={point}: figure is taller than the frame by {-fy}px"
    assert fy + h <= CH, f"{variant} point={point}: figure runs {fy + h - CH}px past the bottom"
