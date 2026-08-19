# pattern: Functional Core
"""When each word appears, paced like someone reading the post aloud.

The text used to arrive a line at a time. Lines are a typesetting artifact —
they fall wherever the column width put them — so the pauses landed in places
the sentence has no business pausing. On a real post the breaks came after
"how", after "how to", and after "I", while the commas after "telephone" and
"road" and the full stop after "neighbour" all sat mid-line and got nothing.

So the pauses follow the punctuation instead. Each word holds the screen for a
moment, and the moment is longer where a speaker would draw breath: a beat at a
comma, a longer one at a full stop, longest at the end of a paragraph the author
chose to break themselves.

Pure, and keyed on the *wrapped* lines rather than the original text: the
renderer reveals a prefix of what is actually drawn, and `wrap` may split a long
token across lines, so a schedule built from the source string would drift out of
step with the frame the moment someone posts a URL.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

# Extra dwell, in units of one word, for what a word ends with. Tuned by reading
# them aloud rather than by theory: the ratios matter, the absolute values do
# not, because the whole schedule is normalised to the time available.
PAUSE = {
    ",": 1.0,
    ";": 1.4,
    ":": 1.4,
    "—": 1.4,
    "–": 1.4,
    ".": 2.0,
    "!": 2.0,
    "?": 2.0,
    "…": 2.2,
}

# A paragraph the author broke themselves is the strongest signal in the text —
# they chose it, unlike every other break here.
PARAGRAPH_PAUSE = 3.0

# Long words take longer to read than short ones, but nowhere near
# proportionally: the eye lands roughly once per word regardless of length.
LENGTH_WEIGHT = 0.035

_CLOSERS = "\"'’”)]}»"


def _dwell(word: str, *, ends_paragraph: bool) -> float:
    """How long this word holds before the next one appears."""
    # Strip closing quotes and brackets before looking at the punctuation:
    # `neighbour."` ends a sentence just as much as `neighbour.` does, and the
    # naive check sees only the quote mark.
    stripped = word.rstrip(_CLOSERS)
    weight = 1.0 + LENGTH_WEIGHT * len(word)
    if stripped:
        weight += PAUSE.get(stripped[-1], 0.0)
    if ends_paragraph:
        weight += PARAGRAPH_PAUSE
    return weight


@dataclass(frozen=True)
class Cadence:
    """A schedule of (characters revealed, fraction of the window elapsed).

    `stops` is ordered and always opens at fraction 0.0, so the first word is on
    screen from the first frame — a beat of blank type at the top of a short
    video reads as a stall rather than as timing.
    """

    stops: tuple[tuple[int, float], ...]
    total: int

    def chars_at(self, progress: float) -> int:
        """Characters visible once `progress` (0..1) of the window has passed."""
        if progress >= 1.0:
            return self.total
        seen = 0
        for chars, at in self.stops:
            if at > progress:
                break
            seen = chars
        return seen


@functools.lru_cache(maxsize=32)
def of(lines: tuple[str, ...]) -> Cadence:
    """Build the schedule for a wrapped block.

    Cached because the caller asks once per frame with the same lines, and the
    answer depends on nothing else.
    """
    # (chars consumed through this word, dwell weight). Character counts run
    # across the wrapped lines exactly as the renderer walks them, so a blank
    # line — a paragraph break the author made — costs its own character and
    # keeps the two in step.
    entries: list[tuple[int, float]] = []
    consumed = 0
    for i, line in enumerate(lines):
        blank_next = i + 1 < len(lines) and not lines[i + 1].strip()
        last_line = i == len(lines) - 1
        if not line.strip():
            consumed += len(line)
            continue
        pos = 0
        words = line.split(" ")
        for j, word in enumerate(words):
            pos += len(word) + (1 if j < len(words) - 1 else 0)
            ends_para = (j == len(words) - 1) and (blank_next or last_line)
            entries.append((consumed + pos, _dwell(word, ends_paragraph=ends_para)))
        consumed += len(line)

    total = consumed
    if not entries:
        return Cadence(((total, 0.0),), total)

    # The first word is on screen at 0.0, so it is every *subsequent* word that
    # has to be paid for — the last word's own dwell buys nothing, since there
    # is nothing after it to wait for.
    budget = sum(w for _, w in entries[:-1]) or 1.0
    stops, elapsed = [], 0.0
    for idx, (chars, weight) in enumerate(entries):
        stops.append((chars, min(1.0, elapsed / budget)))
        if idx < len(entries) - 1:
            elapsed += weight
    return Cadence(tuple(stops), total)


def visible(lines: tuple[str, ...], progress: float) -> tuple[str, ...]:
    """The wrapped block as it stands partway through its reveal.

    Returns one entry per line up to the cursor, the last one truncated. Cutting
    only at word boundaries is what keeps this cheap: the halo behind the type is
    memoised on exactly this tuple, so a per-character reveal would rebuild a
    full-frame blur on nearly every frame — the single most expensive thing in
    the compositor — where per-word rebuilds it once per word.
    """
    budget = of(lines).chars_at(progress)
    out = []
    for line in lines:
        if budget <= 0:
            break
        if budget >= len(line):
            out.append(line)
            budget -= len(line)
        else:
            out.append(line[:budget].rstrip())
            budget = 0
    return tuple(out)
