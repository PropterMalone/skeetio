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
import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

UA = {"User-Agent": "skeetio/0.1 (+public-domain broll fetcher)"}
SEARCH = "https://archive.org/advancedsearch.php"
CACHE = Path.home() / ".cache" / "skeetio" / "broll"


# Licences the Internet Archive records that genuinely permit unrestricted
# reuse. A licence stated as anything else — or not stated at all — is not one
# of these, and archive.org has plenty of both.
#
# This lives here rather than in curate.py, where it started, because curate is
# the *offline pool builder*: gating there screens the library but leaves the
# render path free to stamp "public domain" on any identifier passed to --clip.
# The credit is a claim about someone else's rights, so the check belongs at the
# point the claim is made.
PUBLIC_DOMAIN = ("publicdomain", "public-domain", "cc0", "mark/1.0", "zero/1.0")


def year_from(md: dict) -> str | None:
    """A four-digit year out of archive.org's free-text date fields.

    Taking `date[:4]` looks right and is not: archive.org dates include "ca.
    1943", "1950-1959" and "undated", so the slice yields the truthy string
    "ca. " and the credit renders as "(ca. )" — six of the shipped clips did
    exactly that. Match digits rather than trusting position, and return None
    when there are none rather than a fragment.
    """
    for field in ("year", "date"):
        m = re.search(r"\b(1[5-9]\d{2}|20\d{2})\b", str(md.get(field) or ""))
        if m:
            return m.group(1)
    return None


def is_public_domain(licenceurl: str | None) -> bool:
    """Whether an archive.org `licenseurl` asserts public domain.

    Absent is not public domain. Most of the Prelinger collection is genuinely
    PD and says so; the ones that say nothing are the ones worth stopping on.
    """
    lic = (licenceurl or "").lower()
    return any(tok in lic for tok in PUBLIC_DOMAIN)


@dataclass(frozen=True)
class Clip:
    identifier: str
    title: str
    year: str | None
    path: Path
    collection: str = "Prelinger Archives"
    licenceurl: str = ""

    @property
    def public_domain(self) -> bool:
        return is_public_domain(self.licenceurl)

    @property
    def credit_lines(self) -> tuple[str, str]:
        """What goes on screen, as (work, source). Public domain imposes no
        attribution duty, but naming the source is free and makes the channel
        legible rather than looking like it scraped something.

        Two lines because one does not fit: the credit sits in the clear column
        beside the creature, which is ~350px wide, and the median single-line
        credit is twice that. Splitting also means the rights statement can be
        held to a size that always fits while a long archival title ellipsizes —
        the part that must stay legible is the part making the legal claim.
        """
        y = f" ({self.year})" if self.year else ""
        rights = "public domain" if self.public_domain else "licence unverified"
        return (f"{self.title}{y}", f"{self.collection} · {rights}")


def search(query: str, *, collection: str = "prelinger", rows: int = 40) -> list[dict]:
    params = {
        "q": f'collection:{collection} AND mediatype:movies AND format:"MPEG4"'
        + (f" AND ({query})" if query else ""),
        # licenseurl is requested because the credit line asserts public domain;
        # a search result that cannot be screened on licence is not usable.
        "fl[]": ["identifier", "title", "year", "downloads", "licenseurl"],
        "rows": rows,
        "sort[]": "downloads desc",
        "output": "json",
    }
    url = f"{SEARCH}?{urllib.parse.urlencode(params, doseq=True)}"
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return json.load(r)["response"]["docs"]


COLLECTION_NAMES = {"prelinger": "Prelinger Archives", "nasa": "NASA"}


def fetch(
    identifier: str, *, cache: Path = CACHE, max_bytes: int = 180_000_000,
    collection: str = "prelinger",
) -> Clip:
    """Download the best usable mp4 derivative, cached by identifier."""
    cache.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(
        urllib.request.Request(f"https://archive.org/metadata/{identifier}", headers=UA), timeout=40
    ) as r:
        meta = json.load(r)

    md = meta.get("metadata", {})
    # archive.org answers an unknown identifier with HTTP 200 and an empty JSON
    # object rather than a 404, so indexing meta["files"] blind turns a typo in
    # --clip into a raw KeyError traceback.
    if not meta.get("files"):
        raise LookupError(f"{identifier}: no such item on archive.org (or it has no files)")
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
    # The fallback above deliberately ignores the cap when nothing fits under
    # it, so `pick` may be larger than max_bytes. Take the real ceiling from the
    # file actually chosen: without this the download loop below is unbounded,
    # while post.py's comment cites this function as the bounded network path.
    ceiling = max(max_bytes, int(pick["size"]))

    dest = cache / f"{identifier}.mp4"
    if not dest.exists():
        url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(pick['name'])}"
        # Unique temp per process. A fixed ".part" name lets two concurrent
        # fetches of the same clip interleave into one inode; whichever renames
        # first lands corrupt bytes at `dest`, and since the guard above is
        # `dest.exists()`, that corruption is permanent — every later render
        # silently reuses the broken file. rename(2) within a directory is
        # atomic, so a unique source makes the publish step safe.
        fd, tmp_name = tempfile.mkstemp(dir=cache, prefix=f".{identifier}.", suffix=".part")
        tmp = Path(tmp_name)
        try:
            # Wrap the fd first: if urlopen raises, os.fdopen never runs and the
            # raw descriptor leaks — the finally below removes the file, not it.
            with os.fdopen(fd, "wb") as f:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=300) as r:
                    got = 0
                    while chunk := r.read(1 << 20):
                        got += len(chunk)
                        if got > ceiling:
                            raise OSError(
                                f"{identifier}: download exceeded {ceiling} bytes "
                                f"(Content-Length claimed {pick['size']})"
                            )
                        f.write(chunk)
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)

    year = year_from(md)
    title = md.get("title") or identifier
    if isinstance(title, list):
        title = title[0]
    return Clip(
        identifier, str(title), str(year) if year else None, dest,
        COLLECTION_NAMES.get(collection, collection),
        str(md.get("licenseurl") or ""),
    )


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


def audio_segment(
    path: Path, dest: Path, *, start: float, dur: float, gain: float = 0.5
) -> tuple[Path | None, str]:
    """Lift the clip's own soundtrack for the same span as the picture.

    The archival audio is public domain along with the picture, period-correct
    by construction, and stranger than anything that could be scored for it — a
    1956 orchestral swell under a post about AI avatars is the mismatch engine
    running for free. Ducked, because it is a bed and not the subject.

    Returns (path_or_None, reason). Three outcomes used to collapse into a bare
    None, and the caller reported all of them as "none (source silent)" — so a
    systemic ffmpeg failure was announced to the operator as a property of the
    footage, which is the same mistake curate's silent probe made.
    """
    if not has_audio(path):
        return None, "source has no audio track"
    cmd = [
        "ffmpeg", "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
        "-i", str(path), "-vn",
        "-af", f"volume={gain},afade=t=in:st=0:d=0.4,afade=t=out:st={max(0.0, dur - 0.6):.2f}:d=0.6",
        "-c:a", "aac", "-b:a", "160k", str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip().splitlines()
        return None, f"ffmpeg failed ({err[-1] if err else 'no stderr'})"
    if not (dest.exists() and dest.stat().st_size > 0):
        return None, "ffmpeg wrote no audio data"
    return dest, "clip soundtrack"


def encode(frame_iter, out: Path, size: tuple[int, int], fps: int, *, audio: Path | None = None) -> Path:
    """Pipe RGB frames into x264. Kept deliberately simple: raw frames in,
    h264 out, parameterised on width and height."""
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
