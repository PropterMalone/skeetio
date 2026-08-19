"""The text should pause where a speaker would, not where the column broke.

The reveal used to advance a line at a time. Lines fall wherever the wrap put
them, so on a real post the pauses landed after "how", after "how to", and after
"I", while the commas after "telephone" and "road" and the full stop after
"neighbour" all sat mid-line and got nothing at all.

These tests are about *relative* timing. The absolute weights are taste and will
be retuned; that a comma outlasts a plain word, and a full stop outlasts a
comma, is the thing that must not silently regress.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

import cadence
from skeet_frame import SAFE, fit_text

POST = ("The archive is full of people demonstrating things nobody needed "
        "demonstrated: how to answer a telephone, how to cross a road, how to "
        "be a good neighbour. I find this unreasonably comforting.")


def _wrapped(text: str = POST) -> tuple[str, ...]:
    d = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    _, lines, _ = fit_text(text, "EBGaramond-SemiBold.ttf",
                           (SAFE[2] - SAFE[0], 1126), d, lo=46, hi=118, leading=1.2)
    return tuple(lines)


def _gaps(lines: tuple[str, ...]) -> dict[str, float]:
    """Dwell after each word, keyed by the word."""
    c = cadence.of(lines)
    words = " ".join(lines).split()
    out = {}
    for i in range(len(c.stops) - 1):
        out[words[i]] = c.stops[i + 1][1] - c.stops[i][1]
    return out


def test_punctuation_outlasts_plain_words():
    g = _gaps(_wrapped())
    plain = g["archive"]
    assert g["telephone,"] > plain * 1.5, "a comma did not buy a longer pause"
    assert g["neighbour."] > g["telephone,"], "a full stop did not outlast a comma"
    assert g["demonstrated:"] > plain * 1.5, "a colon did not buy a longer pause"


def test_the_pause_does_not_follow_the_line_break():
    """The actual regression. 'how' and 'how to' end wrapped lines in this post
    and are mid-phrase; they must be unremarkable."""
    lines = _wrapped()
    enders = {ln.split()[-1] for ln in lines if ln.split()}
    assert "how" in enders, "the fixture no longer reproduces the mid-phrase break"

    g = _gaps(lines)
    plain = g["archive"]
    assert g["how"] < plain * 1.4, (
        f"'how' still gets a pause ({g['how']:.3f} vs {plain:.3f} for a plain "
        f"word) — the cadence is following the wrap, not the sentence"
    )


def test_closing_punctuation_does_not_hide_the_sentence_end():
    """`neighbour."` ends a sentence as much as `neighbour.` does; a naive check
    sees only the quote mark."""
    plain = cadence._dwell("word", ends_paragraph=False)
    for w in ('end."', "end.'", "end.)", "end.”"):
        assert cadence._dwell(w, ends_paragraph=False) > plain * 1.5, w


def test_an_author_line_break_is_the_longest_pause_of_all():
    """They chose it, unlike every other break here."""
    assert (cadence._dwell("word", ends_paragraph=True)
            > cadence._dwell("word.", ends_paragraph=False))


# --- the reveal itself ------------------------------------------------------


def test_the_first_word_is_on_screen_immediately():
    """A beat of blank type at the top of an eight-second video reads as a stall,
    not as timing."""
    assert cadence.visible(_wrapped(), 0.0), "the frame opens empty"


def test_everything_is_shown_by_the_end():
    lines = _wrapped()
    assert cadence.visible(lines, 1.0) == lines


def test_the_reveal_only_ever_moves_forward():
    """Anything else is a flicker."""
    lines = _wrapped()
    seen = 0
    for i in range(101):
        n = len("".join(cadence.visible(lines, i / 100)))
        assert n >= seen, f"the text went backwards at progress {i / 100}"
        seen = n


@pytest.mark.parametrize("progress", [i / 40 for i in range(41)])
def test_every_frame_is_a_prefix_of_the_finished_block(progress):
    """The cut must land on the wrapped text exactly, or the partial frame shows
    something the finished one does not."""
    lines = _wrapped()
    shown = cadence.visible(lines, progress)
    assert len(shown) <= len(lines)
    for i, line in enumerate(shown):
        assert lines[i].startswith(line), f"line {i} is not a prefix: {line!r}"


def test_the_cut_never_lands_inside_a_word():
    """Both because half a word is ugly, and because the halo behind the type is
    memoised per distinct reveal — a per-character cut would rebuild a
    full-frame blur on nearly every frame."""
    lines = _wrapped()
    for i in range(201):
        shown = cadence.visible(lines, i / 200)
        if not shown:
            continue
        last, full = shown[-1], lines[len(shown) - 1]
        if last != full:
            assert full[len(last):len(last) + 1] in ("", " "), (
                f"cut mid-word: {last!r} against {full!r}"
            )


def test_distinct_frames_stay_bounded_by_the_word_count():
    """What keeps the blur cache useful. If this grows toward the frame count,
    the reveal has started cutting per character."""
    lines = _wrapped()
    distinct = {cadence.visible(lines, i / 192) for i in range(193)}
    assert len(distinct) <= len(" ".join(lines).split()) + 1


# --- the cases that break a naive implementation ----------------------------


def test_a_url_split_across_lines_stays_in_step():
    """fit_text breaks an over-long token at the character level, so a schedule
    built from the source string rather than the wrapped lines desyncs the
    moment somebody posts a link."""
    lines = _wrapped("look at https://archive.org/details/a-very-long-identifier-that-wraps now")
    assert len(lines) > 1
    for i in range(41):
        for j, line in enumerate(cadence.visible(lines, i / 40)):
            assert lines[j].startswith(line)


def test_a_single_word_post_is_shown_at_once():
    assert cadence.visible(("Hello",), 0.0) == ("Hello",)


def test_an_empty_block_does_not_explode():
    assert cadence.visible((), 0.5) == ()
    assert cadence.of(()).total == 0
