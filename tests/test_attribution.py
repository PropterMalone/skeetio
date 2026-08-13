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
