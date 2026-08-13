"""Type must stay inside the safe area, and the credit must not lie.

YouTube Shorts paints its own chrome over the video: channel name bottom-left,
action rail down the right, progress bar along the bottom. Anything outside SAFE
is covered on the surface this project launches on. The credit line spent the
first release 314px *below* SAFE — the one line naming the source and asserting
public domain was the one line nobody could see.

The credit is also a claim about someone else's rights, so it gets tested as a
claim: the string says "public domain" only when the clip's licence actually
says so.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

from broll import Clip, is_public_domain
from looks import CREDIT_GUTTER, CREDIT_PT, FIG_CX, FLOOR, Mood, compose
from skeet_frame import CH, CW, SAFE, Attribution, Author, load_font

# The widest the creature's bounding box gets across every variant and pose,
# measured by tests/test_figure_bounds.py. The credit column is what is left of
# the safe area to the right of it.
WIDEST_FIGURE = 646


def _credit_column() -> int:
    fx = int(CW * FIG_CX) - WIDEST_FIGURE // 2
    return SAFE[2] - (fx + WIDEST_FIGURE) - CREDIT_GUTTER


def _pool_clips() -> list[Clip]:
    import json

    data = json.loads(
        (Path(__file__).resolve().parent.parent / "assets" / "broll-prelinger.json").read_text()
    )
    return [
        Clip(c["identifier"], c["title"], c.get("year"), Path("/nonexistent"),
             "Prelinger Archives", c.get("licenseurl", ""))
        for c in data["clips"]
    ]


def test_rights_line_fits_the_column_the_creature_leaves():
    """The line making the legal claim is never ellipsized, so it must fit
    unaided at the widest the creature ever gets."""
    font = load_font("Inter-Regular.ttf", CREDIT_PT)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    column = _credit_column()

    for clip in _pool_clips():
        rights = clip.credit_lines[1]
        w = d.textlength(rights, font=font)
        assert w <= column, (
            f"{clip.identifier}: rights line {w:.0f}px wide but the creature "
            f"leaves only {column}px. Lower looks.CREDIT_PT or shorten the line — "
            f"do not ellipsize it, it is the part making the legal claim."
        )


@pytest.mark.parametrize(
    "licenceurl,expected",
    [
        ("http://creativecommons.org/publicdomain/mark/1.0/", True),
        ("https://creativecommons.org/publicdomain/zero/1.0/", True),
        ("http://creativecommons.org/licenses/by-nc-sa/4.0/", False),
        ("http://creativecommons.org/licenses/by/4.0/", False),
        ("", False),
        (None, False),
    ],
)
def test_licence_rule(licenceurl, expected):
    """Absent is not public domain — that is the case the pool actually hit."""
    assert is_public_domain(licenceurl) is expected


def test_credit_claims_public_domain_only_when_the_licence_says_so():
    nc = Clip("x", "A Film", "1955", Path("/nonexistent"), "Prelinger Archives",
              "http://creativecommons.org/licenses/by-nc-sa/4.0/")
    pd = Clip("y", "A Film", "1955", Path("/nonexistent"), "Prelinger Archives",
              "http://creativecommons.org/publicdomain/mark/1.0/")
    assert "public domain" not in nc.credit_lines[1]
    assert "public domain" in pd.credit_lines[1]


def test_every_pool_clip_is_public_domain():
    """The shipped library must not contain a clip the renderer would refuse."""
    bad = [c.identifier for c in _pool_clips() if not c.public_domain]
    assert not bad, f"pool clips with a non-public-domain licence: {bad}"


def test_compose_draws_the_credit_inside_the_safe_area():
    """End-to-end: render a frame and find the credit's ink.

    A geometry assertion on constants would pass while compose() drew somewhere
    else entirely — which is the exact way this bug survived the first release.
    """
    plate = Image.new("RGB", (CW, CH), (0, 0, 0))
    figure = Image.new("RGBA", (WIDEST_FIGURE, 780), (0, 0, 0, 0))
    clip = Clip("z", "A Film About Something", "1955", Path("/nonexistent"),
                "Prelinger Archives", "http://creativecommons.org/publicdomain/mark/1.0/")

    quote = Attribution("hello world", Author("N", "n.bsky.social"))
    frame = compose(plate, figure, quote, clip.credit_lines)

    # The credit is the only ink below the byline in the bottom-right quadrant.
    import numpy as np

    a = np.array(frame.convert("L"))
    region = a[SAFE[3] - 60:CH, SAFE[2] - 400:CW]
    rows = np.nonzero(region.max(axis=1) > 60)[0]
    assert len(rows), "no credit ink found anywhere near the bottom of the safe area"

    lowest = SAFE[3] - 60 + int(rows.max())
    assert lowest <= SAFE[3] + 4, (
        f"credit ink reaches y={lowest}, past the safe area bottom at {SAFE[3]} — "
        f"it will sit under the Shorts progress bar"
    )
