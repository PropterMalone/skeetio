# pattern: Imperative Shell
"""Render one Bluesky post to a 9:16 short over public-domain b-roll.

    python3 render/make_video.py \
        --post https://bsky.app/profile/someone.bsky.social/post/3kxyz \
        --clip Designfo1956 --start 118 --out /tmp/out.mp4

The post is addressed by URL or at:// URI and never by handle-plus-text. An
earlier version took those separately and promptly put one person's words on
screen under another person's name — in a project about asking permission
first. Text, author, and avatar now arrive together or not at all.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import broll
import pair
import post as P
from looks import compose
import exits
from skeet_frame import CH, CW, load_font, unsupported_chars

# How far down a post's own clip ranking to walk when archive.org will not serve
# the ones above. Bounded: past a few, the far end is not having an item-level
# problem, and hammering it with a fourth 180 MB request will not help.
CLIP_TRIES = 4


def select_clip(picks, *, override_start, dur, want_audio):
    """Walk this post's ranking until a clip can actually be used.

    Returns (clip, in-point, how it was chosen), or None if nothing in the top
    CLIP_TRIES qualifies.

    Two reasons to fall through, and they are not the same kind of reason.
    Unreachable is about the far end: archive.org fails per item and for hours,
    ToNewHor1940 served 503 all afternoon while everything else answered, and
    because pairing is deterministic a post that drew it was wedged rather than
    delayed. Silent is about the clip itself and is a fixed property, so skipping
    it stays deterministic where the availability skip does not.

    Lifted out of main() so it can be tested at all. Both failures reached
    production while a suite of 128 tests passed, because everything either of
    them touches lived inside an argparse-to-exit-code function that no test
    could call.
    """
    for n, pick in enumerate(picks[:CLIP_TRIES]):
        try:
            # Pass the pick's own collection: the pool is a merge, and without
            # this every clip credited "Prelinger Archives" including NASA.
            candidate = broll.fetch(pick.identifier, collection=pick.collection)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, LookupError) as e:
            print(f"{pick.identifier} unavailable ({type(e).__name__}) — "
                  f"falling through to the next clip this post ranked", file=sys.stderr)
            continue
        at = override_start if override_start is not None else pick.start
        if want_audio:
            # Silence is grounds for falling through, not just for a log line.
            # Detecting a dead track and then encoding it anyway is exactly how
            # the first silent video reached a stranger: the check was added, the
            # message changed, and the output did not.
            peak = broll.peak_dbfs(candidate.path, start=at, dur=dur)
            if peak is not None and peak < broll.SILENT_DBFS:
                print(f"{pick.identifier} has no usable audio ({peak:.0f} dBFS) — "
                      f"falling through to the next clip this post ranked", file=sys.stderr)
                continue
        return candidate, at, ("drawn" if n == 0 else f"drawn, fallback {n}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--post", required=True, help="bsky.app URL or at:// URI")
    ap.add_argument("--clip", help="archive.org identifier; omitted means draw one from the library")
    ap.add_argument("--collection", default="prelinger",
                    help="archive the --clip identifier belongs to, for the on-screen credit "
                         "(default: prelinger; ignored when the clip is drawn from the library)")
    ap.add_argument("--start", type=float,
                    help="in-point in seconds; defaults to the curated in-point when the clip is "
                         "drawn from the library, or 60s when --clip names one explicitly")
    ap.add_argument("--dur", type=float, default=10.0, help="length in seconds (default 10)")
    ap.add_argument("--fps", type=int, default=24, help="frame rate (default 24)")
    ap.add_argument("--silent", action="store_true", help="drop the archival audio bed")
    ap.add_argument("--generic", action="store_true",
                    help="no likeness at all — a disc keyed to the author's DID instead of their "
                         "picture, for when you have no permission to use one")
    ap.add_argument("--out", required=True, help="path to write the mp4 to")
    ap.add_argument("--manifest",
                    help="write a JSON record of what was rendered — source post, author, clip, "
                         "in-point. For a caller that needs to log provenance rather than parse "
                         "this program's stdout.")
    args = ap.parse_args()

    try:
        sk = P.fetch(args.post)
        # Words, name and face in one call. There is deliberately no path here
        # that names a handle, a body of text, or a picture separately.
        quote = P.quote(sk, likeness=not args.generic)
    except (urllib.error.URLError, TimeoutError, OSError, KeyError, LookupError) as e:
        # Without this the interpreter exits 1, which is in none of the
        # documented groups — so a bot's tens-digit dispatch has no defined
        # behaviour and a DNS blip reads as a permanent failure. KeyError and
        # LookupError are here because a deleted post comes back as a well-formed
        # response with nothing in it, not as a transport error.
        print(f"could not read {args.post}: {e}", file=sys.stderr)
        return exits.FETCH_FAILED
    if quote.avatar is None and not args.generic:
        # They have no avatar set, and this run wanted one. --generic is the mode
        # that was never going to draw their picture, so it is the remediation;
        # falling through to a blank disc silently would make the no-likeness
        # mode indistinguishable from a fetch that quietly found nothing.
        print(f"@{sk.handle} has no avatar set — use --generic", file=sys.stderr)
        return exits.NO_AVATAR
    print(f"post: @{sk.handle} · {sk.likes} likes")
    # Truncated: the operator needs to confirm they fetched the right post, not
    # to spool a third party's full text into scrollback and any redirected log.
    preview = sk.text.replace("\n", " ")
    print(f"text: {preview[:72]}{'…' if len(preview) > 72 else ''}")
    if not sk.stands_alone:
        print(
            "note: this post leans on its parent or an image, so it may not read "
            "alone. Rendering anyway — pull in the post above if it needs context.",
            file=sys.stderr,
        )

    if not sk.text.strip():
        print("post has no text (image-only?) — nothing to render", file=sys.stderr)
        return exits.NO_TEXT
    missing = unsupported_chars(sk.text, load_font("EBGaramond-SemiBold.ttf", 64))
    if missing:
        # The bundled faces are Latin-only and PIL does no fallback, so these
        # would draw as empty boxes under the author's real name. Refuse.
        shown = " ".join(sorted(missing)[:12])
        print(
            f"the bundled font has no glyphs for: {shown}\n"
            "rendering would show empty boxes where their words are — refusing.\n"
            "bundle a Noto fallback covering this script to support it.",
            file=sys.stderr,
        )
        return exits.UNRENDERABLE_SCRIPT

    if args.clip:
        try:
            clip = broll.fetch(args.clip, collection=args.collection)
        except (urllib.error.URLError, TimeoutError, OSError, KeyError, LookupError) as e:
            # No fallback when the operator named the clip. They asked for that
            # one, and quietly substituting another is not answering the request.
            print(f"could not fetch {args.clip}: {e}", file=sys.stderr)
            return exits.CLIP_FETCH_FAILED
        start = args.start if args.start is not None else 60.0
        source = "specified"
    else:
        picked = select_clip(
            pair.ranked(sk.uri, pair.load()),
            override_start=args.start, dur=args.dur, want_audio=not args.silent,
        )
        if picked is None:
            print(f"none of this post's top {CLIP_TRIES} clips could be used", file=sys.stderr)
            return exits.CLIP_FETCH_FAILED
        clip, start, source = picked

    if not clip.public_domain:
        # Library clips were screened at curation, but --clip takes any
        # archive.org identifier and bypasses that entirely. Refusing here rather
        # than softening the credit line: an NC term is wrong for a monetised
        # channel, and "licence unverified" burned into someone's video is not a
        # thing to ship either.
        print(
            f"{clip.identifier}: licence is "
            f"{clip.licenceurl or 'not stated'}, which is not public domain.\n"
            "Every frame credits the clip as public domain, so rendering this "
            "would put a false legal claim on screen — refusing.",
            file=sys.stderr,
        )
        return exits.CLIP_NOT_PUBLIC_DOMAIN

    # Clamp the in-point so the whole window fits inside the film. The library
    # stores a good start time but not a duration, and jitter can push the
    # window past the end — which silently yields a short video rather than an
    # error, so it has to be caught here.
    clip_secs = broll.duration(clip.path)
    if clip_secs < args.dur:
        # The clamp below can only shift the window, not create footage. A clip
        # shorter than --dur used to just stop early and report nothing but the
        # file size, so the operator got a short video and no reason why.
        print(
            f"{clip.identifier} is {clip_secs:.0f}s but --dur is {args.dur:.0f}s — "
            f"the render would stop early. Lower --dur or pick a longer clip.",
            file=sys.stderr,
        )
        return exits.CLIP_TOO_SHORT
    start = max(0.0, min(start, max(0.0, clip_secs - args.dur - 1.0)))
    print(f"clip ({source}): {clip.identifier} · {clip_secs:.0f}s · {clip.title[:44]}")
    print(f"in-point: {start:.1f}s")

    if args.generic:
        print("generic — no likeness used")

    total = int(args.dur * args.fps)

    def gen():
        src = broll.frames(clip.path, (CW, CH), start=start, dur=args.dur + 0.5, fps=args.fps)
        for n, plate in enumerate(src):
            if n >= total:
                break
            t = n / args.fps
            reveal = min(1.0, 0.34 + (t / (args.dur * 0.45)) * 0.66)
            yield compose(plate, quote, clip.credit_lines, reveal=reveal)
            if n % 48 == 0:
                print(f"  frame {n}/{total}", flush=True)

    out = Path(args.out)
    bed = None
    if not args.silent:
        bed, why = broll.audio_segment(
            clip.path, out.with_suffix(".bed.m4a"), start=start, dur=args.dur
        )
        print(f"audio bed: {why}")
    broll.encode(gen(), out, (CW, CH), args.fps, audio=bed)
    if bed:
        bed.unlink(missing_ok=True)
    print(f"wrote {out} ({out.stat().st_size/1e6:.1f} MB)")

    if args.manifest:
        # Written last, so its existence means the render finished. A caller
        # logging provenance needs the source post and the clip, and the only
        # other way to get them is to scrape this program's stdout — which makes
        # a print statement part of the contract without anyone deciding that.
        Path(args.manifest).write_text(json.dumps({
            "rendered_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_uri": sk.uri,
            "source_cid": sk.cid,
            "author_did": sk.did,
            "author_handle": sk.handle,
            "clip": clip.identifier,
            "collection": clip.collection,
            "start": round(start, 2),
            "dur": args.dur,
            "generic": bool(args.generic),
            "out": str(out),
        }, indent=2))
        print(f"manifest → {args.manifest}")
    return exits.OK


if __name__ == "__main__":
    raise SystemExit(main())
