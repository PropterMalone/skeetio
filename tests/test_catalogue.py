"""What the pool promises the renderer, and what the credit says about it.

These cover the class of bug this project keeps producing: a guard that runs
without error while doing nothing, and a claim on screen that no code path
checks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

import pair
from broll import year_from
from skeet_frame import load_font, unsupported_chars

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def test_excluded_clips_never_reach_the_pool():
    """The quarantine file was once named broll-held.json, which pair.load()'s
    own glob matched — the file holding the excluded material was being read
    straight back in. Assert the exclusion, not the filename."""
    excluded = json.loads((ASSETS / "excluded-clips.json").read_text())
    # "blocked" entries read "Identifier (Title)"; "held" entries are objects.
    ids = {entry.split()[0] for entry in excluded["blocked"]}
    ids |= {c["identifier"] for c in excluded["held"]}
    assert len(ids) >= 5, "excluded-clips.json lists nothing — this test would pass vacuously"

    pool_ids = {c["identifier"] for c in pair.load()}
    leaked = ids & pool_ids
    assert not leaked, f"excluded clips present in the render pool: {sorted(leaked)}"


def test_quarantine_file_is_not_matched_by_the_library_glob():
    """The second lock: the loader skips files with no "clips" key, but the file
    must not be glob-visible in the first place."""
    globbed = {p.name for p in ASSETS.glob("broll-*.json")}
    assert "excluded-clips.json" not in globbed
    assert not any(n.startswith("broll-") and "exclud" in n for n in globbed)


def test_every_pooled_clip_carries_a_collection():
    """Without this the credit says "Prelinger Archives" for NASA footage."""
    for clip in pair.load():
        assert clip.get("collection"), f"{clip['identifier']} has no collection"


def test_pick_carries_the_collection_through():
    pool = [{"identifier": "x", "title": "T", "best_start": 30.0, "collection": "nasa"}]
    assert pair.choose("at://example/post/1", pool).collection == "nasa"


@pytest.mark.parametrize(
    "md,expected",
    [
        ({"year": "1955"}, "1955"),
        ({"date": "1955-03-01"}, "1955"),
        ({"date": "ca. 1943"}, "1943"),
        ({"date": "1950-1959"}, "1950"),
        ({"date": "undated"}, None),
        ({}, None),
    ],
)
def test_year_from_never_yields_a_fragment(md, expected):
    """`date[:4]` on "ca. 1943" gives the truthy string "ca. ", which rendered
    on screen as "(ca. )" for six of the shipped clips."""
    assert year_from(md) == expected


def test_no_pooled_clip_renders_a_broken_year():
    for clip in pair.load():
        y = clip.get("year")
        assert y is None or (y.isdigit() and len(y) == 4), (
            f"{clip['identifier']} has year {y!r}, which renders as a fault on screen"
        )


def test_tofu_guard_fails_closed_when_it_cannot_fingerprint_notdef(monkeypatch):
    """The guard's whole history is fail-open. If the .notdef sentinels stop
    returning a mask, it must refuse rather than pass every script on earth."""
    import skeet_frame

    monkeypatch.setattr(skeet_frame, "_glyph_sig", lambda font, ch: None)
    font = load_font("EBGaramond-SemiBold.ttf", 64)
    with pytest.raises(RuntimeError, match="tofu guard"):
        unsupported_chars("hello", font)


def test_tofu_guard_still_catches_unrenderable_script():
    font = load_font("EBGaramond-SemiBold.ttf", 64)
    assert unsupported_chars("日本語のポスト", font)
    assert not unsupported_chars("a plain latin post", font)


def test_pruning_the_pool_does_not_reshuffle_unaffected_posts():
    """The pool gets pruned — most recently to remove internment propaganda —
    and an index-into-pool-length draw remaps nearly every post when it does,
    silently changing videos people already agreed to."""
    pool = [
        {"identifier": f"clip{i:02d}", "title": "T", "best_start": 30.0, "collection": "prelinger"}
        for i in range(40)
    ]
    uris = [f"at://did:plc:x/app.bsky.feed.post/{i}" for i in range(300)]
    before = {u: pair.choose(u, pool).identifier for u in uris}

    dropped = before[uris[0]]
    pruned = [c for c in pool if c["identifier"] != dropped]
    after = {u: pair.choose(u, pruned).identifier for u in uris}

    moved = [u for u in uris if before[u] != after[u]]
    # Only posts that had drawn the removed clip may move.
    assert all(before[u] == dropped for u in moved), (
        f"{len(moved)} posts changed clip but only those drawing {dropped} should have"
    )
    assert moved, "the post that drew the dropped clip must be repaired"


def test_pairing_is_stable_across_pool_order():
    pool = [
        {"identifier": f"clip{i:02d}", "title": "T", "best_start": 30.0, "collection": "prelinger"}
        for i in range(20)
    ]
    uri = "at://did:plc:x/app.bsky.feed.post/abc"
    assert pair.choose(uri, pool).identifier == pair.choose(uri, list(reversed(pool))).identifier
