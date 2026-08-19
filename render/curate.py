# pattern: Imperative Shell
"""Score public-domain clips for use as skeet backdrops, and emit a library.

Not every clip composites. The author's picture, handle and the credit sit in
the bottom third and the type runs across the middle, so a clip is good here
when it *moves*, is bright enough to survive a scrim, and is quiet exactly where
things get placed on top of it. Talking heads, title cards, and dense text
frames all fail despite being fine films. It also needs audible sound: the
archival audio arrives free with the picture, and that is what keeps these from
being silent videos.

Scoring streams a handful of frames directly off archive.org with ffmpeg's HTTP
seek, so vetting a hundred candidates costs a few megabytes rather than a few
gigabytes of downloads.

    python3 render/curate.py --collection prelinger --rows 60 --out assets/broll-prelinger.json

The output name is not free: pair.load() globs `assets/broll-*.json`, so a
catalogue written to `broll.json` is curated, reported as a success, and then
silently ignored by every render.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import re as _re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import broll

PROBE_W, PROBE_H = 192, 144

# Two tiers, because one list cannot do both jobs.
#
# The screen exists because the scorer selects FOR this material rather than
# against it: it rewards motion, edge detail, brightness and a quiet lower
# third, and mid-century propaganda is well-made — steady camera, high
# production value, busy frames. The top-ranked clip in the first shipped
# library was the US government's 1943 film justifying Japanese-American
# internment.
#
# The failure mode is specific: pairing is uniform random and the author's real
# name and face are on screen, so the pairing reads as the channel saying
# something about that person.
#
# The policy, plainly: racist propaganda is out. Everything else edgy is a
# judgment call about how a given clip actually scans, which a regex cannot
# make — so HOLD keeps those out of the pool and names them, for a human to
# admit or reject once, explicitly. Silently dropping a camp artifact like
# "Boys Beware" or a period travelogue is its own kind of wrong.
BLOCK = _re.compile(
    r"""
      \b nazi \b | hitler | third \s+ reich | \b gestapo | \b fascis
    | \b intern(ment|ee)s? \b | relocation \s+ (center|centre|camp)
    | \b japanese \s+ relocation | \b my \s+ japan | know \s+ your \s+ enemy
    | \b jap \b | \b japs \b | \b nip \b | \b negro | \b coon | \b darkie
    | colored \s+ (people|folks?) | segregat | eugenic | miscegen
    | racial \s+ (hygiene|purity|superiority) | white \s+ supremac
    | lynch | massacre | atrocit | genocide | concentration \s+ camp
    """,
    _re.I | _re.X,
)

HOLD = _re.compile(
    r"""
      \b war \b | wartime | combat | infantry | bombing | invasion | propagand
    | atom(ic)? \s* bomb | hydrogen \s* bomb | hiroshima | nagasaki | nuclear
    | civil \s+ defense | duck \s+ and \s+ cover | air \s+ raid
    | homosexual | \b deviat | \b pervert | delinquen
    | \b strip(per|tease|ping) | burlesq | \b nude | nudist | girlie | sexploit
    | venereal | syphilis | abortion | childbirth | surgical | autopsy | cadaver
    | disaster | \b riot | \b crash | explo(de|des|sion|sive) | funeral | epidemic
    """,
    _re.I | _re.X,
)

# The licence rule now lives in broll (`broll.is_public_domain`), next to the
# credit line that makes the claim — screening the pool here is not enough on
# its own, because --clip takes any identifier and never passes through
# curation. Referenced rather than restated so the two cannot drift apart.
# NC is wrong for a monetised channel specifically, which is why "some licence"
# is not the bar.


def _mp4_url(identifier: str) -> tuple[str, dict]:
    req = urllib.request.Request(
        f"https://archive.org/metadata/{identifier}", headers=broll.UA
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        meta = json.load(r)
    mp4s = [f for f in meta["files"] if f["name"].lower().endswith(".mp4") and f.get("size")]
    if not mp4s:
        raise LookupError("no mp4")
    pick = max(mp4s, key=lambda f: int(f.get("height") or 0) * int(f.get("width") or 0))
    url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(pick['name'])}"
    return url, meta.get("metadata", {})


def _grab(url: str, t: float, n: int = 4, *, window: float = 2.0) -> list[np.ndarray]:
    """Pull `n` frames spread across `window` seconds from time `t`.

    Spread, not consecutive: adjacent frames are 1/30s apart and differ by
    almost nothing, so a consecutive pair scores every clip at zero motion and
    the ranking silently collapses to brightness. Sampling half a second apart
    measures whether the shot actually goes anywhere.
    """
    fps = n / window
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-t", f"{window:.2f}", "-i", url,
        "-frames:v", str(n), "-vf", f"fps={fps:.3f},scale={PROBE_W}:{PROBE_H},format=rgb24",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0:
        # Raise rather than return short. A throttled or 403'd fetch returns
        # empty stdout, which the caller reads as "fewer than 2 frames" and
        # prints as `skip` — reporting a systemic network failure as a property
        # of the footage, and quietly curating a library shaped by which
        # requests happened to succeed.
        err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(
            f"ffmpeg exited {proc.returncode} probing at {t:.0f}s: "
            f"{err[-1] if err else 'no stderr'}"
        )
    out = proc.stdout
    sz = PROBE_W * PROBE_H * 3
    return [
        np.frombuffer(out[i * sz : (i + 1) * sz], dtype=np.uint8).reshape(PROBE_H, PROBE_W, 3).astype(np.float32)
        for i in range(len(out) // sz)
    ]


def screen(haystack: str, *, admit: bool = False) -> str:
    """What the subject screen does with this text: block, hold, admitted, pass.

    A pure function so the one property that matters can actually be tested:
    **--admit reaches HOLD and can never reach BLOCK.** HOLD means "no human has
    looked at this yet" — a judgment call the README says is yours to make. BLOCK
    means "no pairing defends this". An --admit that could override the second
    would quietly turn a policy into a flag.
    """
    if BLOCK.search(haystack):
        return "block"
    if HOLD.search(haystack):
        return "admitted" if admit else "hold"
    return "pass"


def score_clip(identifier: str, *, probes: int = 5, admit: bool = False) -> Score | None:
    # Failures are reported, not swallowed. A network outage, a throttle, or a
    # missing ffprobe all used to print the same "skip" as a genuinely bad clip,
    # so a systemic failure read as "this batch of footage was poor".
    try:
        url, md = _mp4_url(identifier)
    except Exception as e:
        print(f"      metadata failed: {type(e).__name__}: {e}", flush=True)
        return None

    # Skip the first and last tenth: titles at the head, credits at the tail.
    try:
        dur = float(md.get("runtime_secs") or 0) or _duration_via_url(url)
    except Exception as e:
        print(f"      duration failed: {type(e).__name__}: {e}", flush=True)
        return None
    if dur < 40:
        return None

    title_for_screen = md.get("title") or identifier
    if isinstance(title_for_screen, list):
        title_for_screen = title_for_screen[0]
    haystack = f"{title_for_screen} {identifier} {md.get('description') or ''}"
    if isinstance(md.get("subject"), (list, str)):
        haystack += " " + " ".join(
            md["subject"] if isinstance(md["subject"], list) else [md["subject"]]
        )
    verdict = screen(haystack, admit=admit)
    if verdict == "block":
        print(f"      BLOCKED (subject): {str(title_for_screen)[:52]}", flush=True)
        return None
    if verdict == "hold":
        print(f"      HELD for sign-off: {str(title_for_screen)[:52]}", flush=True)
        print("        (re-run with --admit <identifier> if you have reviewed it)", flush=True)
        return None
    if verdict == "admitted":
        print(f"      ADMITTED by hand: {str(title_for_screen)[:52]}", flush=True)

    lic = (md.get("licenseurl") or "").lower()
    if not broll.is_public_domain(lic):
        print(
            f"      EXCLUDED (licence not public domain: {lic or 'none stated'}): "
            f"{str(title_for_screen)[:40]}",
            flush=True,
        )
        return None

    # Audio, screened as a quality bar rather than a correctness one. The
    # renderer has its own -35 dBFS floor for tracks that are outright dead;
    # this is stricter because the bed is ducked ~6 dB on the way in, so a clip
    # peaking at -20 lands near -26 in the mix — audible on a measurement,
    # inaudible on a phone. Two thresholds because they answer different
    # questions: "is this broken" and "is this good enough to keep".
    #
    # Screened here and not at render time because a clip's loudness is a fixed
    # property, so keeping it out of the library leaves pairing deterministic.
    peak = _peak_via_url(url)
    if peak is not None and peak < AUDIO_FLOOR_DBFS:
        print(f"      EXCLUDED (audio {peak:.0f} dBFS, below the {AUDIO_FLOOR_DBFS:.0f} bar): "
              f"{str(title_for_screen)[:40]}", flush=True)
        return None

    best: tuple[float, float, dict] | None = None
    for i in range(probes):
        t = dur * (0.15 + 0.7 * (i / max(1, probes - 1)))
        try:
            fr = _grab(url, t)
        except Exception as e:
            print(f"      probe @{t:.0f}s failed: {type(e).__name__}: {e}", flush=True)
            continue
        if len(fr) < 2:
            continue
        a = fr[0]
        grey = a.mean(axis=2)
        motion = float(
            np.mean([np.abs(fr[i + 1] - fr[i]).mean() for i in range(len(fr) - 1)])
        ) / 255.0
        brightness = float(grey.mean()) / 255.0
        contrast = float(grey.std()) / 128.0
        # The bottom third carries the author's picture, their handle and the
        # credit line; reward a quiet, even floor. This was originally scored
        # because the creature stood there — the creature is parked, but the
        # lower third is if anything busier now, and the credit is the one
        # element that must stay readable rather than merely visible.
        floor = grey[int(PROBE_H * 0.62) :, :]
        floor_calm = 1.0 - min(1.0, float(floor.std()) / 90.0)

        # "Eye-catching" as edge energy: how much is actually going on. A static
        # talking head and a busy street can share a brightness and a contrast
        # and pull the eye completely differently. Deliberately not scored on
        # saturation — the mid-century monochrome instructional films are a
        # large part of the charm and must not be penalised for being grey.
        detail = float(
            np.abs(np.diff(grey, axis=0)).mean() + np.abs(np.diff(grey, axis=1)).mean()
        ) / 255.0

        # Motion is the point, so it dominates. Brightness is a gate more than a
        # gradient — very dark plates die under the scrim, very bright ones eat
        # the type — so it is scored as distance from a comfortable mid.
        bright_fit = 1.0 - min(1.0, abs(brightness - 0.46) / 0.46)
        total = (
            motion * 3.4
            + min(detail, 0.18) * 5.0
            + bright_fit * 1.5
            + contrast * 0.7
            + floor_calm * 1.1
        )
        cand = (total, t, dict(motion=motion, brightness=brightness, contrast=contrast,
                               floor_calm=floor_calm, detail=detail))
        if best is None or total > best[0]:
            best = cand

    if best is None:
        return None
    total, t, m = best
    title = md.get("title") or identifier
    if isinstance(title, list):
        title = title[0]
    year = broll.year_from(md)
    return Score(
        identifier=identifier,
        title=str(title),
        year=year,
        url=url,
        licenseurl=md.get("licenseurl") or "",
        best_start=round(t, 1),
        motion=round(m["motion"], 4),
        brightness=round(m["brightness"], 3),
        contrast=round(m["contrast"], 3),
        floor_calm=round(m["floor_calm"], 3),
        detail=round(m["detail"], 4),
        total=round(total, 3),
    )


# Peak below which a clip is not worth keeping, in dBFS. Stricter than
# broll.SILENT_DBFS: that one asks whether a track is dead, this asks whether it
# will still be there after the bed is ducked into the mix.
AUDIO_FLOOR_DBFS = -18.0


def _peak_via_url(url: str, *, start: float = 60.0, secs: float = 45.0) -> float | None:
    """Loudest sample in a span, measured over the network without downloading
    the whole film. None means it could not be measured, which is treated as
    usable — a curator refusing every clip because ffmpeg moved a log line would
    be a worse outcome than admitting a quiet one."""
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start:.0f}", "-t", f"{secs:.0f}",
         "-i", url, "-vn", "-af", "volumedetect", "-f", "null", os.devnull],
        capture_output=True, text=True, timeout=240,
    )
    m = _re.search(r"max_volume:\s*(-?[\d.]+) dB", out.stderr or "")
    return float(m.group(1)) if m else None


def _duration_via_url(url: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", url],
        capture_output=True, text=True, timeout=120,
    )
    return float(out.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="prelinger")
    ap.add_argument("--query", default="")
    ap.add_argument("--rows", type=int, default=40)
    ap.add_argument("--keep", type=int, default=20)
    ap.add_argument("--admit", action="append", default=[], metavar="IDENTIFIER",
                    help="admit a clip the subject screen would HOLD, having reviewed it "
                         "yourself. Repeatable. Cannot admit a BLOCKed clip — that tier is "
                         "material no pairing defends, and it is not a judgment call.")
    ap.add_argument("--out", required=True,
                    help="catalogue path; the filename must match broll-*.json or pair.load() "
                         "will not pick it up")
    args = ap.parse_args()

    # Refuse up front rather than after the network spend. pair.load() globs
    # `broll-*.json`, so any other name curates a library, prints a success line,
    # and is then silently ignored by every render — which is what the module's
    # own documented invocation used to do.
    if not (Path(args.out).name.startswith("broll-") and args.out.endswith(".json")):
        print(
            f"--out {args.out} would never be loaded: pair.load() globs "
            f"assets/broll-*.json. Name it broll-{args.collection}.json.",
            file=sys.stderr,
        )
        return 2

    admitted = set(args.admit)
    if admitted:
        print(f"admitting {len(admitted)} held clip(s) by hand: {', '.join(sorted(admitted))}",
              flush=True)

    cands = broll.search(args.query, collection=args.collection, rows=args.rows)
    print(f"scoring {len(cands)} candidates from {args.collection}…", flush=True)

    scored: list[Score] = []
    for i, c in enumerate(cands, 1):
        s = score_clip(c["identifier"], admit=c["identifier"] in admitted)
        flag = f"{s.total:6.2f}" if s else "  skip"
        print(f"  [{i:>3}/{len(cands)}] {flag}  {c['identifier'][:38]}", flush=True)
        if s:
            scored.append(s)

    scored.sort(key=lambda s: s.total, reverse=True)
    keep = scored[: args.keep]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"collection": args.collection, "query": args.query, "clips": [asdict(s) for s in keep]},
            indent=2,
        )
    )
    print(f"\nkept {len(keep)} of {len(scored)} scored → {out}")
    for s in keep[:10]:
        print(f"  {s.total:5.2f}  mot={s.motion:.3f} det={s.detail:.3f} brt={s.brightness:.2f} "
              f"flr={s.floor_calm:.2f}  {s.title[:40]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
