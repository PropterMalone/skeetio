# pattern: Imperative Shell
"""Score public-domain clips for use as skeet backdrops, and emit a library.

Not every clip composites. The figure stands in the bottom third and the type
sits across the middle, so a clip is good here when it *moves*, is bright enough
to survive a scrim, and is quiet exactly where things get placed on top of it.
Talking heads, title cards, and dense text frames all fail despite being fine
films.

Scoring streams a handful of frames directly off archive.org with ffmpeg's HTTP
seek, so vetting a hundred candidates costs a few megabytes rather than a few
gigabytes of downloads.

    python3 render/curate.py --collection prelinger --rows 60 --out assets/broll.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import broll

PROBE_W, PROBE_H = 192, 144


@dataclass
class Score:
    identifier: str
    title: str
    year: str | None
    url: str
    best_start: float
    motion: float
    brightness: float
    contrast: float
    floor_calm: float
    detail: float
    total: float


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
    out = subprocess.run(cmd, capture_output=True, timeout=180).stdout
    sz = PROBE_W * PROBE_H * 3
    return [
        np.frombuffer(out[i * sz : (i + 1) * sz], dtype=np.uint8).reshape(PROBE_H, PROBE_W, 3).astype(np.float32)
        for i in range(len(out) // sz)
    ]


def score_clip(identifier: str, *, probes: int = 5) -> Score | None:
    try:
        url, md = _mp4_url(identifier)
    except Exception:
        return None

    # Skip the first and last tenth: titles at the head, credits at the tail.
    try:
        dur = float(md.get("runtime_secs") or 0) or _duration_via_url(url)
    except Exception:
        return None
    if dur < 40:
        return None

    best: tuple[float, float, dict] | None = None
    for i in range(probes):
        t = dur * (0.15 + 0.7 * (i / max(1, probes - 1)))
        try:
            fr = _grab(url, t)
        except Exception:
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
        # The bottom third carries the figure; reward a quiet, even floor.
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
    year = md.get("year") or (md.get("date") or "")[:4] or None
    return Score(
        identifier=identifier,
        title=str(title),
        year=str(year) if year else None,
        url=url,
        best_start=round(t, 1),
        motion=round(m["motion"], 4),
        brightness=round(m["brightness"], 3),
        contrast=round(m["contrast"], 3),
        floor_calm=round(m["floor_calm"], 3),
        detail=round(m["detail"], 4),
        total=round(total, 3),
    )


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
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cands = broll.search(args.query, collection=args.collection, rows=args.rows)
    print(f"scoring {len(cands)} candidates from {args.collection}…", flush=True)

    scored: list[Score] = []
    for i, c in enumerate(cands, 1):
        s = score_clip(c["identifier"])
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
