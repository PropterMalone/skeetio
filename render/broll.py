# pattern: Imperative Shell
"""Fetch and decode public-domain b-roll from the Internet Archive.

The insight this serves: the footage does not need to be *about* the post. It
needs to move. Motion holds the eye long enough for a reader to finish the text,
which is the whole reason karaoke videos put anything behind the lyrics at all.
Relevance is a bonus; movement is the requirement.

Prelinger Archives is the right well — 10,460 items, all
`creativecommons.org/licenses/publicdomain/`, direct mp4 URLs, no key. Its
mid-century educational register also does comic work for free: a 2026 skeet
over 1953 posture-training footage is a mismatch nobody has to write.

NASA's 13,728 items are the same deal when the brief wants pretty over strange.
"""

from __future__ import annotations

import json
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

UA = {"User-Agent": "skeetio/0.1 (+public-domain broll fetcher)"}
SEARCH = "https://archive.org/advancedsearch.php"
CACHE = Path.home() / ".cache" / "skeetio" / "broll"


@dataclass(frozen=True)
class Clip:
    identifier: str
    title: str
    year: str | None
    path: Path

    @property
    def credit(self) -> str:
        """What goes on screen. Public domain imposes no attribution duty, but
        naming the source is free and makes the channel legible rather than
        looking like it scraped something."""
        y = f" ({self.year})" if self.year else ""
        return f"{self.title}{y} · Prelinger Archives · public domain"


def search(query: str, *, collection: str = "prelinger", rows: int = 40) -> list[dict]:
    params = {
        "q": f'collection:{collection} AND mediatype:movies AND format:"MPEG4"'
        + (f" AND ({query})" if query else ""),
        "fl[]": ["identifier", "title", "year", "downloads"],
        "rows": rows,
        "sort[]": "downloads desc",
        "output": "json",
    }
    url = f"{SEARCH}?{urllib.parse.urlencode(params, doseq=True)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.load(r)["response"]["docs"]


def fetch(identifier: str, *, cache: Path = CACHE, max_bytes: int = 180_000_000) -> Clip:
    """Download the best usable mp4 derivative, cached by identifier."""
    cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        urllib.request.Request(f"https://archive.org/metadata/{identifier}", headers=UA), timeout=40
    ) as r:
        meta = json.load(r)

    md = meta.get("metadata", {})
    mp4s = [f for f in meta["files"] if f["name"].lower().endswith(".mp4") and f.get("size")]
    if not mp4s:
        raise LookupError(f"{identifier}: no mp4 derivative")
    # Prefer the highest resolution under a sane cap. The instinct to grab the
    # smallest derivative is wrong here: these get upscaled to 1080 wide, and
    # the 512kb Prelinger encodes are 320x240, which turns to mush.
    def rank(f: dict) -> tuple[int, int]:
        px = int(f.get("height") or 0) * int(f.get("width") or 0)
        return (px, -int(f["size"]))

    usable = [f for f in mp4s if int(f["size"]) <= max_bytes] or [
        min(mp4s, key=lambda f: int(f["size"]))
    ]
    pick = max(usable, key=rank)

    dest = cache / f"{identifier}.mp4"
    if not dest.exists():
        url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(pick['name'])}"
        tmp = dest.with_suffix(".part")
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r, tmp.open("wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.rename(dest)

    year = md.get("year") or (md.get("date") or "")[:4] or None
    title = md.get("title") or identifier
    if isinstance(title, list):
        title = title[0]
    return Clip(identifier, str(title), str(year) if year else None, dest)


def duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(out.stdout.strip())


def frames(
    path: Path, size: tuple[int, int], *, start: float, dur: float, fps: int
):
    """Yield PIL frames, scaled to cover `size` and centre-cropped.

    Cover-crop rather than letterbox: these sources are 4:3 and a 9:16 letterbox
    would leave two dead bands where the whole point is a moving field.
    """
    w, h = size
    vf = (
        f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},format=rgb24"
    )
    cmd = [
        "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(path), "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    n = w * h * 3
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=n * 2)
    assert p.stdout is not None
    try:
        while True:
            buf = p.stdout.read(n)
            if len(buf) < n:
                break
            yield Image.frombytes("RGB", (w, h), buf)
    finally:
        # A caller that stops early leaves ffmpeg writing into a closed pipe,
        # which prints muxer errors that look like real failures. Kill it first,
        # then close, so an abandoned decode exits quietly.
        if p.poll() is None:
            p.kill()
        p.stdout.close()
        p.wait()


def has_audio(path: Path) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return bool(out.stdout.strip())


def audio_segment(path: Path, dest: Path, *, start: float, dur: float, gain: float = 0.5) -> Path | None:
    """Lift the clip's own soundtrack for the same span as the picture.

    The archival audio is public domain along with the picture, period-correct
    by construction, and stranger than anything that could be scored for it — a
    1956 orchestral swell under a post about AI avatars is the mismatch engine
    running for free. Ducked, because it is a bed and not the subject.
    """
    if not has_audio(path):
        return None
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(path), "-vn",
        "-af", f"volume={gain},afade=t=in:st=0:d=0.4,afade=t=out:st={max(0.0, dur - 0.6):.2f}:d=0.6",
        "-c:a", "aac", "-b:a", "160k", str(dest),
    ]
    if subprocess.run(cmd, capture_output=True).returncode != 0:
        return None
    return dest if dest.exists() and dest.stat().st_size > 0 else None


def encode(frame_iter, out: Path, size: tuple[int, int], fps: int, *, audio: Path | None = None) -> Path:
    """Pipe RGB frames into x264. Kept deliberately simple: raw RGB frames in, h264 out."""
    w, h = size
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
    ]
    if audio:
        cmd += ["-i", str(audio), "-c:a", "aac", "-b:a", "160k", "-shortest"]
    cmd += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-f", "mp4", str(out),
    ]
    with subprocess.Popen(cmd, stdin=subprocess.PIPE) as p:
        assert p.stdin is not None
        for fr in frame_iter:
            p.stdin.write(fr.convert("RGB").tobytes())
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError("ffmpeg encode failed")
    return out
