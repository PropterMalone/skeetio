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


# --- the subject screen's two tiers, and what --admit may reach ---------------

from curate import screen  # noqa: E402


def test_admit_reaches_hold_but_never_block():
    """HOLD means "nobody has looked at this yet" — a judgment call, and the
    README says it is yours to make. BLOCK means "no pairing defends this".
    An --admit that could override BLOCK would turn a policy into a flag."""
    blocked = "Japanese Relocation 1943 internment propaganda"
    assert screen(blocked) == "block"
    assert screen(blocked, admit=True) == "block", (
        "--admit reached the BLOCK tier; that tier is not a judgment call"
    )


def test_admit_is_what_releases_a_held_clip():
    held = "Variety Girls burlesque striptease"
    assert screen(held) == "hold"
    assert screen(held, admit=True) == "admitted"


def test_ordinary_footage_passes_untouched():
    assert screen("Design for Dreaming 1956 General Motors Motorama") == "pass"


def test_the_screen_cannot_tell_anti_racist_film_from_racist_one():
    """`Don't Be a Sucker` is a 1947 US Army film AGAINST racism. It trips the
    screen, and that is the correct behaviour — a keyword screen cannot read
    stance, so it defers to a human instead of guessing. Pinned so nobody
    'fixes' the false positive by teaching the regex to guess."""
    assert screen("Don't Be a Sucker 1947 racism prejudice propaganda") in ("block", "hold")


# --- the fallback that keeps an item-level outage from wedging a post -------
# archive.org served 503 for ToNewHor1940 for hours while every other item
# answered normally. Pairing is deterministic, so posts that drew it were not
# delayed, they were permanently unrenderable.


def test_the_ranking_is_stable_for_a_post():
    """Determinism is the whole point of this module. If the order moved between
    calls, a re-render would hand someone a different video."""
    pool = pair.load()
    uri = "at://did:plc:x/app.bsky.feed.post/abc"
    first = [p.identifier for p in pair.ranked(uri, pool)]
    assert first == [p.identifier for p in pair.ranked(uri, pool)]
    assert len(first) == len(set(first)) == len(pool), "the ranking dropped or repeated a clip"


def test_the_top_of_the_ranking_is_what_choose_returns():
    """Otherwise the fallback path and the normal path disagree about which clip
    a post 'really' drew, and the ledger records one while the video shows the
    other."""
    pool = pair.load()
    for uri in (f"at://did:plc:x/app.bsky.feed.post/{n}" for n in ("a", "b", "c", "d")):
        assert pair.ranked(uri, pool)[0] == pair.choose(uri, pool)


def test_different_posts_rank_differently():
    """A shared ranking would mean the fallback sent every wedged post to the
    same clip, which is how you get thirty identical videos."""
    pool = pair.load()
    seconds = {pair.ranked(f"at://did:plc:x/app.bsky.feed.post/{n}", pool)[1].identifier
               for n in "abcdefgh"}
    assert len(seconds) > 1


def test_removing_a_clip_only_moves_the_posts_that_drew_it():
    """The property the whole highest-random-weight scheme exists for, now
    checked against the ranking rather than only the winner — consent attaches
    to a specific rendering, so an unrelated post must not be reshuffled."""
    pool = pair.load()
    victim = pair.choose("at://did:plc:x/app.bsky.feed.post/aaa", pool).identifier
    pruned = [c for c in pool if c["identifier"] != victim]

    moved, unmoved = 0, 0
    for n in range(60):
        uri = f"at://did:plc:x/app.bsky.feed.post/p{n}"
        before = pair.choose(uri, pool).identifier
        after = pair.choose(uri, pruned).identifier
        if before == victim:
            moved += 1
        else:
            assert before == after, f"{uri} was reshuffled by an unrelated prune"
            unmoved += 1
    assert unmoved, "the sample never exercised the unmoved case"


def test_a_dead_clip_falls_through_to_this_posts_second_choice():
    """End to end on the actual failure: the top clip cannot be fetched, so the
    render must use the next one *this post* ranked — not a random other one,
    and not nothing at all."""
    pool = pair.load()
    uri = "at://did:plc:x/app.bsky.feed.post/wedged"
    order = pair.ranked(uri, pool)
    dead = order[0].identifier

    survivors = [p for p in order if p.identifier != dead]
    assert survivors[0].identifier == order[1].identifier, (
        "skipping the dead clip did not land on this post's own second choice"
    )
