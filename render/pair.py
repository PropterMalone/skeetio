# pattern: Imperative Shell
"""Choose which archival clip backs a given post.

`choose()` is pure; `load()` reads the library off disk, which is why this file
is Shell and not Core. It was tagged Core, and skeet_frame's docstring defines
that as pure with respect to the filesystem — a reader is entitled to trust the
header.

The maintainer's call, 2026-08-13: **random**. Relevance is not the point — the karaoke
principle is that the footage has to move, not that it has to mean anything. A
matcher that always lands on the nose stops being funny by the fourth video,
whereas an occasional coincidence is the one people screenshot.

Random, but *deterministically* random: the seed is the post URI, so the same
post always draws the same clip. That matters more than it sounds. People keep
these files, a re-render after a code change should not silently hand someone a
different video, and a bug is impossible to chase if the inputs reshuffle every
run.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

LIBRARY = Path(__file__).resolve().parent.parent / "assets"


@dataclass(frozen=True)
class Pick:
    identifier: str
    title: str
    year: str | None
    start: float
    # Which archive the clip came from. Carried per pick because the pool is a
    # *merge* of libraries: without it every clip credited "Prelinger Archives"
    # on screen, including NASA footage.
    collection: str = "prelinger"


def _seeded(key: str) -> random.Random:
    """A Random keyed to a stable digest of `key`.

    Python's own hash() is salted per process, so seeding from it would give a
    different clip on every run — exactly the non-determinism this module exists
    to avoid.
    """
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


def _weight(post_uri: str, identifier: str) -> str:
    """This post's affinity for this one clip, independent of every other clip."""
    return hashlib.sha256(f"{post_uri}\x00{identifier}".encode()).hexdigest()


def load(*paths: Path) -> list[dict]:
    """Merge one or more curated libraries into a single pool.

    Merging rather than choosing lets Prelinger and NASA sit in one draw, which
    is what makes the register swing between earnest hygiene film and orbital
    footage without anyone selecting for it.
    """
    files = list(paths) or sorted(LIBRARY.glob("broll-*.json"))
    pool: dict[str, dict] = {}
    for f in files:
        data = json.loads(f.read_text())
        # A file without "clips" is not a library. Skipping rather than raising
        # matters because the quarantine file for excluded clips was originally
        # named broll-held.json, which this very glob matched — so the file
        # holding the material deliberately kept OUT of the pool was being read
        # into it, and only failed loudly because it had no "clips" key. It is
        # now assets/excluded-clips.json, and this guard is the second lock.
        if "clips" not in data:
            continue
        # curate.py records the collection once at file level; stamp it onto each
        # clip on the way into the merged pool, because after the merge there is
        # no file left to ask.
        collection = data.get("collection", "prelinger")
        for c in data["clips"]:
            pool[c["identifier"]] = {"collection": collection, **c}  # dedupe across collections
    return list(pool.values())


def choose(post_uri: str, pool: list[dict], *, jitter: float = 25.0) -> Pick:
    """Draw a clip and an in-point for this post.

    The in-point wanders around the window the curator liked rather than sitting
    exactly on it: two posts drawing the same clip should not open on the same
    frame.

    Only the HEAD is clamped here, to keep the in-point clear of a film's titles.
    The tail clamp needs the clip's duration, which means probing the file, so it
    lives in the caller (make_video, against broll.duration). Said "clamped" flatly
    before, and a caller who trusted that gets a silently short or empty video.
    """
    if not pool:
        raise LookupError("empty b-roll pool — run render/curate.py first")

    # Highest-random-weight, not an index into the pool. rng.choice(pool) draws
    # from the pool's *length*, so adding or removing a single clip remaps
    # nearly every post — and this pool gets pruned, most recently to take
    # internment propaganda out of it. That would have silently changed the
    # video an approved beta subject had already agreed to.
    #
    # Scoring each clip independently means a post's pick only moves if the clip
    # it drew is the one that left, or a new clip happens to outscore it. Nothing
    # else in the library can disturb it.
    clip = max(pool, key=lambda c: _weight(post_uri, c["identifier"]))
    rng = _seeded(f"{post_uri}\x00{clip['identifier']}")
    start = max(8.0, clip["best_start"] + rng.uniform(-jitter, jitter))
    return Pick(
        clip["identifier"], clip["title"], clip.get("year"), round(start, 1),
        clip.get("collection", "prelinger"),
    )
