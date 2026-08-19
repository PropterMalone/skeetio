"""Measure every library clip's audio, so the dead ones can be pruned.

    python3 tools/audit-audio.py

Downloads each clip once (the render cache keeps them, and eviction bounds it)
and reports the peak over the span a render would actually use. curate.py
screens new clips on the way in; this exists for a library that predates that
screen, and for re-checking after archive.org re-derives an item.

"unfetchable" is not a verdict. archive.org fails per item and for hours at a
time, so a clip that cannot be reached today may be fine tomorrow — do not
prune on it.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))
import broll
import pair

OUT = Path(__file__).with_name("audio-audit.json")
results = {}
clips = pair.load()
print(f"auditing {len(clips)} clips", flush=True)

for i, c in enumerate(sorted(clips, key=lambda x: x["identifier"]), 1):
    ident = c["identifier"]
    try:
        clip = broll.fetch(ident, collection=c.get("collection", "prelinger"))
    except Exception as e:
        results[ident] = {"state": "unfetchable", "error": type(e).__name__}
        print(f"{i:3}/{len(clips)} {ident:24} UNFETCHABLE ({type(e).__name__})", flush=True)
        continue

    if not broll.has_audio(clip.path):
        results[ident] = {"state": "no_track"}
        print(f"{i:3}/{len(clips)} {ident:24} no audio track", flush=True)
        continue

    # Measure around the curated in-point — that is the span a render uses, and
    # a film can be silent over its titles and fine later.
    peak = broll.peak_dbfs(clip.path, start=float(c.get("best_start", 60)), dur=20.0)
    whole = broll.peak_dbfs(clip.path)
    dead = peak is not None and peak < broll.SILENT_DBFS
    results[ident] = {"state": "dead" if dead else "ok", "peak_at_start": peak, "peak_whole": whole}
    print(f"{i:3}/{len(clips)} {ident:24} peak {peak} dBFS at in-point"
          f"{'   <-- DEAD' if dead else ''}", flush=True)

OUT.write_text(json.dumps(results, indent=2))
dead = [k for k, v in results.items() if v["state"] == "dead"]
nof = [k for k, v in results.items() if v["state"] == "no_track"]
unf = [k for k, v in results.items() if v["state"] == "unfetchable"]
print(f"\nDONE  ok={len(results)-len(dead)-len(nof)-len(unf)} dead={len(dead)} "
      f"no_track={len(nof)} unfetchable={len(unf)}")
print("dead:", dead)
print("no_track:", nof)
print("unfetchable:", unf)
