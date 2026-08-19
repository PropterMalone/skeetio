"""Words and the name on them travel together, everywhere, always.

The project's own CLAUDE.md carries this as a hard rule, because an earlier
make_video took --handle and --text independently and put one person's words on
screen under another person's name. post.py closed that at the fetch boundary;
compose() had quietly reopened it with a loose parameter each.

This is a signature test rather than a behaviour test on purpose. The bug is not
that some call is wrong today — it is that a wrong call is *expressible*.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

import looks
import skeet_frame
from skeet_frame import CH, CW, SAFE, Attribution, Author


class _FakePost:
    text = "words that belong to someone"
    display_name = "Someone"
    handle = "someone.bsky.social"
    did = "did:plc:someone"


def test_attribution_of_a_post_keeps_the_words_with_the_name():
    a = Attribution.of(_FakePost())
    assert a.text == _FakePost.text
    assert a.author.handle == _FakePost.handle


def test_attribution_is_immutable():
    a = Attribution.of(_FakePost())
    with pytest.raises(Exception):
        a.text = "someone else's words"


def test_attribution_carries_the_did_for_seeding():
    """--generic keys its disc on the DID. Handles rotate when someone moves to
    a custom domain, which would silently change a person's colour."""
    assert Attribution.of(_FakePost()).author.did == _FakePost.did


# The pairs that must never appear as separate parameters on one function. The
# words and the name are the original misattribution; the face and the name are
# the identical hazard one layer down, and it opened up the moment the creature
# was parked and compose() started drawing the picture itself.
INSEPARABLE = [
    ({"text", "author"}, "the words and the name"),
    ({"avatar", "author"}, "the face and the name"),
    ({"avatar", "text"}, "the face and the words"),
    ({"pfp", "author"}, "the face and the name"),
    ({"pfp", "text"}, "the face and the words"),
]


@pytest.mark.parametrize("module", [looks, skeet_frame])
def test_no_renderer_takes_identity_apart(module):
    """The invariant, enforced against the signatures rather than one call site.

    If a function needs two halves of someone's identity, it must take the
    single record that binds them. Passing them apart is what has to stay
    inexpressible.

    Scanning both drawing modules, not just `looks`: the earlier version of this
    test looked at `looks` alone for {text, author}, which means it would have
    fired on neither the old `figure` parameter nor the `avatar` one that
    replaced it. A guard that cannot fire is this project's signature failure.
    """
    offenders = []
    for name, fn in inspect.getmembers(module, inspect.isfunction):
        params = set(inspect.signature(fn).parameters)
        for pair, why in INSEPARABLE:
            if pair <= params:
                offenders.append(f"{module.__name__}.{name}{inspect.signature(fn)} — {why}")
    assert not offenders, (
        "these take someone's identity apart, which is how the misattribution "
        f"happened: {offenders}"
    )


def test_the_separability_scan_can_actually_fire():
    """Guards the guard. The scan above asserts an absence, so a bug that made
    it inspect nothing would look exactly like a pass."""
    def bad(text, author):
        raise AssertionError("never called — this exists only for its signature")

    found = [p for p, _ in INSEPARABLE if p <= set(inspect.signature(bad).parameters)]
    assert found, "the scan no longer recognises the shape it exists to reject"


def test_compose_requires_an_attribution():
    params = inspect.signature(looks.compose).parameters
    assert "quote" in params
    assert not ({"text", "author", "avatar", "pfp", "figure"} & set(params))


# --- --generic: the mode you use when you have no permission ----------------
# The mode already shipped once as a documented consent feature that did
# nothing, so the guarantee gets a lock rather than a promise. Asserted against
# the *finished frame* rather than against the function that draws the disc: a
# test on the drawing helper would keep passing if compose() started blending
# the avatar in somewhere else, which is exactly how the first one got through.

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def _magenta_pixels(frame: Image.Image) -> int:
    a = np.array(frame.convert("RGB"))
    return int(((a[:, :, 0] > 200) & (a[:, :, 1] < 60) & (a[:, :, 2] > 200)).sum())


def _frame(avatar: Image.Image | None) -> Image.Image:
    quote = Attribution(
        "words that belong to someone",
        Author("Someone", "someone.bsky.social", did="did:plc:generic-test"),
        avatar,
    )
    plate = Image.new("RGB", (CW, CH), (0, 0, 0))
    return looks.compose(plate, quote, ("A Film", "Prelinger Archives · public domain"))


def test_generic_renders_no_pixel_of_the_avatar():
    """A saturated avatar no ordinary palette produces. If any of it reaches the
    frame, the render is carrying a likeness it was told not to use."""
    magenta = Image.new("RGB", (400, 400), (255, 0, 255))

    assert _magenta_pixels(_frame(None)) == 0, (
        "--generic drew the avatar's own pixels — it is supposed to key the disc "
        "to the DID and use no likeness at all"
    )
    # Guards the test itself: if the probe colour stopped surviving even the
    # normal path, the assertion above would pass for the wrong reason.
    assert _magenta_pixels(_frame(magenta)) > 0, (
        "probe colour did not survive a normal render, so the generic "
        "assertion proves nothing"
    )


def _disc_region(frame: Image.Image) -> np.ndarray:
    """Just the disc. The handle is drawn on the frame too, so comparing whole
    frames would only prove that changing a handle changes the handle."""
    box = (SAFE[0], SAFE[3] - looks.PFP, SAFE[0] + looks.PFP, SAFE[3])
    return np.array(frame.crop(box).convert("RGB"))


def test_generic_disc_is_stable_per_author_and_differs_between_authors():
    """Same person, same colour, every time — that is what makes the disc a
    stand-in for someone rather than decoration. Keyed on the DID, so it must
    survive a handle change."""
    plate = Image.new("RGB", (CW, CH), (0, 0, 0))
    credit = ("A Film", "Prelinger Archives · public domain")

    def disc_for(handle: str, did: str) -> np.ndarray:
        quote = Attribution("words", Author("Someone", handle, did=did), None)
        return _disc_region(looks.compose(plate, quote, credit))

    baseline = disc_for("someone.bsky.social", "did:plc:generic-test")
    moved = disc_for("someone.example.com", "did:plc:generic-test")
    other = disc_for("other.bsky.social", "did:plc:someone-else")

    assert np.array_equal(moved, baseline), (
        "the disc changed when only the handle changed — it is seeded on the "
        "handle somewhere, and handles rotate when someone moves to a custom domain"
    )
    assert not np.array_equal(other, baseline), (
        "two different DIDs produced the same disc, so it is not keyed to anyone"
    )
