# pattern: Functional Core
"""Choose which archival clip backs a given post.

**Random**, by design. Relevance is not the point — the karaoke
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


def _seeded(key: str) -> random.Random:
    """A Random keyed to a stable digest of `key`.

    Python's own hash() is salted per process, so seeding from it would give a
    different clip on every run — exactly the non-determinism this module exists
    to avoid.
    """
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:16], 16))


def load(*paths: Path) -> list[dict]:
    """Merge one or more curated libraries into a single pool.

    Merging rather than choosing lets Prelinger and NASA sit in one draw, which
    is what makes the register swing between earnest hygiene film and orbital
    footage without anyone selecting for it.
    """
    files = list(paths) or sorted(LIBRARY.glob("broll-*.json"))
    pool: dict[str, dict] = {}
    for f in files:
        for c in json.loads(f.read_text())["clips"]:
            pool[c["identifier"]] = c  # dedupe: a clip may appear in two collections
    return list(pool.values())


def choose(post_uri: str, pool: list[dict], *, jitter: float = 25.0) -> Pick:
    """Draw a clip and an in-point for this post.

    The in-point wanders around the window the curator liked rather than sitting
    exactly on it: two posts drawing the same clip should not open on the same
    frame. Jitter is clamped to stay clear of a film's head and tail, where
    titles and credits live and nothing moves.
    """
    if not pool:
        raise LookupError("empty b-roll pool — run render/curate.py first")
    rng = _seeded(post_uri)
    clip = rng.choice(sorted(pool, key=lambda c: c["identifier"]))
    start = max(8.0, clip["best_start"] + rng.uniform(-jitter, jitter))
    return Pick(clip["identifier"], clip["title"], clip.get("year"), round(start, 1))
