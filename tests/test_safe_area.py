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
from looks import CREDIT_COL, CREDIT_GUTTER, CREDIT_PT, IDENT_COL, PFP, PFP_GAP, compose
from skeet_frame import CH, CW, SAFE, Attribution, Author, load_font


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


def test_rights_line_fits_its_reserved_column():
    """The line making the legal claim is never ellipsized, so it must fit
    unaided inside the column reserved for it.

    The column used to be whatever the creature left over. It is now declared
    outright, which means this test also guards against someone shrinking
    CREDIT_COL to make room for a longer handle — the handle is the thing that
    gives, not the rights line."""
    font = load_font("Inter-Regular.ttf", CREDIT_PT)
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))

    for clip in _pool_clips():
        rights = clip.credit_lines[1]
        w = d.textlength(rights, font=font)
        assert w <= CREDIT_COL, (
            f"{clip.identifier}: rights line {w:.0f}px wide but the reserved "
            f"column is {CREDIT_COL}px. Lower looks.CREDIT_PT, widen CREDIT_COL, "
            f"or shorten the line — do not ellipsize it, it is the part making "
            f"the legal claim."
        )


def test_the_identity_block_and_the_credit_column_cannot_overlap():
    """Both are bottom-anchored in the same strip. The arithmetic that keeps
    them apart lives in looks.IDENT_COL, so it gets checked rather than trusted:
    a wider disc or a bigger gutter silently eats the handle's room."""
    ident_right = SAFE[0] + PFP + PFP_GAP + IDENT_COL
    credit_left = SAFE[2] - CREDIT_COL
    assert ident_right + CREDIT_GUTTER <= credit_left, (
        f"the identity block reaches x={ident_right} and the credit column "
        f"starts at x={credit_left} — they collide, and the handle will draw "
        f"underneath the rights line"
    )
    assert IDENT_COL > 120, (
        f"IDENT_COL is down to {IDENT_COL}px, which is not enough for a handle "
        f"— the ellipsis would start eating it at almost any length"
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
    clip = Clip("z", "A Film About Something", "1955", Path("/nonexistent"),
                "Prelinger Archives", "http://creativecommons.org/publicdomain/mark/1.0/")

    quote = Attribution("hello world", Author("N", "n.bsky.social", did="did:plc:n"))
    frame = compose(plate, quote, clip.credit_lines)

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


def test_an_account_with_no_display_name_is_not_printed_twice():
    """The AppView reports a display-name-less account with its handle *as* the
    display name. Drawing both prints the same string twice, stacked, which
    reads as a rendering fault — and it happens to everyone who never set one."""
    import numpy as np

    plate = Image.new("RGB", (CW, CH), (0, 0, 0))
    credit = ("A Film", "Prelinger Archives · public domain")

    def ink_rows(display_name: str) -> int:
        q = Attribution("hello world", Author(display_name, "n.bsky.social", did="did:plc:n"))
        a = np.array(compose(plate, q, credit).convert("L"))
        # The identity column only, clear of the disc and of the credit column.
        strip = a[SAFE[3] - PFP:SAFE[3], SAFE[0] + PFP:SAFE[2] - CREDIT_COL]
        return int((strip.max(axis=1) > 60).sum())

    same = ink_rows("n.bsky.social")
    distinct = ink_rows("A Real Name")
    assert same < distinct, (
        f"a handle-as-display-name drew {same} rows of type against {distinct} for a "
        f"real display name — it is being printed twice"
    )


def test_a_realistic_handle_is_never_ellipsized():
    """The handle is the attribution. A shortened display name is cosmetic; a
    shortened handle points at an account that is not the author's — and
    @proptermalone.bsky.social overflowed the column at the original 28pt, so
    this shipped once."""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    for handle in ("a.bsky.social", "starcountr.bsky.social", "proptermalone.bsky.social",
                   "averylongishhandle.bsky.social", "someone.customdomain.example"):
        for pt in range(28, 19, -2):
            f = load_font("Inter-Regular.ttf", pt)
            if d.textlength(f"@{handle}", font=f) <= IDENT_COL:
                break
        else:
            pytest.fail(f"@{handle} does not fit at any size down to the floor")
        assert d.textlength(f"@{handle}", font=f) <= IDENT_COL, (
            f"@{handle} is {d.textlength(f'@{handle}', font=f):.0f}px at {pt}pt "
            f"against a {IDENT_COL}px column — it will be cut"
        )


def test_the_identity_block_clears_the_credit_vertically():
    """Both live in the bottom strip, and a width budget alone is not enough.

    Constraining the identity to a column beside the credit stopped them
    overlapping but left the handle sitting at the credit's own height, 24px
    away — legible, and reading as one crowded row rather than two separate
    things. It also charged the display name for an obstacle well below it,
    cutting names to 366px when 770 were free.

    Asserted on the rendered frame rather than on the constants, because the
    arithmetic being wrong is exactly the thing the constants would agree with.
    """
    import numpy as np

    plate = Image.new("RGB", (CW, CH), (0, 0, 0))
    clip = Clip("z", "A Film About Something Rather Long Indeed", "1955", Path("/nonexistent"),
                "Prelinger Archives", "http://creativecommons.org/publicdomain/mark/1.0/")
    quote = Attribution(
        "hello world",
        Author("post malone ergo propter malone", "proptermalone.bsky.social", did="did:plc:p"),
    )
    # Differential, not positional. The display name now legitimately runs into
    # the right-hand columns *because* it sits above the credit, so splitting
    # the strip by x would measure the fix as a failure. Rendering once without
    # a credit and once with isolates each block's rows without assuming where
    # either one lives.
    strip = slice(SAFE[3] - PFP, SAFE[3])
    band = slice(SAFE[0] + PFP, SAFE[2])

    def rows(credit_lines) -> set[int]:
        a = np.array(compose(plate, quote, credit_lines).convert("L"))
        return set(np.nonzero(a[strip, band].max(axis=1) > 60)[0].tolist())

    ident_rows = rows(("", ""))
    both_rows = rows(clip.credit_lines)
    cred_rows = both_rows - ident_rows

    assert ident_rows and cred_rows, "expected ink from both blocks"
    assert max(ident_rows) < min(cred_rows), (
        f"identity ink reaches row {max(ident_rows)} and the credit starts at "
        f"{min(cred_rows)} — they share a line and read as one crowded row"
    )


def test_a_real_display_name_is_not_truncated():
    """The name that prompted this: 31 characters, 544px, against a column that
    used to be 366 — so it rendered as 'post malone ergo pr…' on a real video."""
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    font = load_font("Inter-SemiBold.ttf", 34)
    name = "post malone ergo propter malone"
    ix = SAFE[0] + PFP + PFP_GAP
    assert d.textlength(name, font=font) <= SAFE[2] - ix, (
        "the display name no longer fits even the full width, so the layout "
        "cannot be sized to hold it"
    )
