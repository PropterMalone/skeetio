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
from skeet_frame import Attribution, Author


class _FakePost:
    text = "words that belong to someone"
    display_name = "Someone"
    handle = "someone.bsky.social"


def test_attribution_of_a_post_keeps_the_words_with_the_name():
    a = Attribution.of(_FakePost())
    assert a.text == _FakePost.text
    assert a.author.handle == _FakePost.handle


def test_attribution_is_immutable():
    a = Attribution.of(_FakePost())
    with pytest.raises(Exception):
        a.text = "someone else's words"


def test_no_renderer_takes_text_and_author_as_separate_parameters():
    """The invariant, enforced against the signatures rather than one call site.

    If a function needs both a body of text and an author, it must take the
    single record that binds them. Passing them apart is what has to stay
    inexpressible.
    """
    offenders = []
    for name, fn in inspect.getmembers(looks, inspect.isfunction):
        params = set(inspect.signature(fn).parameters)
        if {"text", "author"} <= params:
            offenders.append(f"looks.{name}{inspect.signature(fn)}")
    assert not offenders, (
        "these take the words and the name apart, which is how the "
        f"misattribution happened: {offenders}"
    )


def test_compose_requires_an_attribution():
    params = inspect.signature(looks.compose).parameters
    assert "quote" in params
    assert "text" not in params and "author" not in params


# --- --generic: the mode you use when you have no permission ----------------
# Not covered by any other test, and figure.py carries THREE independent
# `if generic:` branches — one per variant. The mode already shipped once as a
# documented consent feature that did nothing, so the guarantee gets a lock
# rather than a promise: a refactor reopening any one branch must fail here.

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

import figure as figure_mod  # noqa: E402
from figure import draw_figure, skin_from_pfp  # noqa: E402
from make_video import FIG, pose_at  # noqa: E402


@pytest.mark.parametrize("variant", ["face", "belly", "crab"])
def test_generic_renders_no_pixel_of_the_avatar(monkeypatch, variant):
    """A saturated avatar no ordinary palette produces. If any of it survives
    into the layer, the creature is carrying a likeness."""
    monkeypatch.setattr(figure_mod, "SS", 1)
    magenta = (255, 0, 255)
    pfp = Image.new("RGB", (400, 400), magenta)
    skin = skin_from_pfp(pfp, seed="did:plc:generic-test")

    def magenta_pixels(generic: bool) -> int:
        im = draw_figure(pfp, FIG, variant=variant, skin=skin, generic=generic,
                         pose=pose_at(0.0, True))
        a = np.array(im.convert("RGB"))
        return int(((a[:, :, 0] > 200) & (a[:, :, 1] < 60) & (a[:, :, 2] > 200)).sum())

    assert magenta_pixels(generic=True) == 0, (
        f"{variant}: --generic drew the avatar's own pixels — it is supposed to "
        f"use the palette and no likeness at all"
    )
    # Guards the test itself: if the probe colour stopped surviving even the
    # non-generic path, the assertion above would pass for the wrong reason.
    assert magenta_pixels(generic=False) > 0, (
        f"{variant}: probe colour did not survive a normal render, so the "
        f"generic assertion proves nothing"
    )
