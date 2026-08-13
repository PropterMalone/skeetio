# pattern: Imperative Shell
"""Upload a rendered short to Bluesky as a native video blob.

Bluesky will not play video from an external URL — a link gets a static card.
Inline playback requires the file to go through the video service and be
referenced as an `app.bsky.embed.video` blob, which is what this does.

**This tool does not post.** It authenticates, uploads, waits for processing,
and prints the finished blob plus the exact record needed to publish. Creating
the post record is a separate, explicit step behind `--create-post`, because a
public post is an outbound message and those are the operator's to send.

    python3 render/publish.py --video ~/renders/skeetio-demo.mp4 \
        --env ~/.config/skeetvideo/.env --alt "..."
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PDS = "https://bsky.social"
VIDEO = "https://video.bsky.app"
VIDEO_DID = "did:web:video.bsky.app"
UA = {"User-Agent": "skeetio/0.1"}


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip("'\"")
    return out


def _req(url: str, *, method: str = "GET", token: str | None = None,
         body: bytes | None = None, ctype: str | None = None,
         ok_codes: tuple[int, ...] = ()) -> dict:
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        # Some statuses carry a usable payload (409 already_exists returns the
        # completed job). An expected status with an unparseable body is still
        # a real failure, so it falls through to the raise.
        parsed = None
        if e.code in ok_codes:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
        if parsed is not None:
            return parsed
        # Never echo the request body — it may carry credentials.
        raise RuntimeError(
            f"{method} {urllib.parse.urlsplit(url).path} -> {e.code}: {raw[:400]}"
        ) from None


def login(identifier: str, password: str) -> dict:
    return _req(
        f"{PDS}/xrpc/com.atproto.server.createSession",
        method="POST",
        body=json.dumps({"identifier": identifier, "password": password}).encode(),
        ctype="application/json",
    )


def pds_did(did: str) -> str:
    """The DID of the PDS that hosts this account.

    Needed because `uploadVideo` writes the blob to the *user's own PDS* on
    their behalf, so its token must be audienced there rather than at the video
    service. Read it out of the DID document instead of hardcoding a host —
    accounts migrate between PDSes, and a stale constant fails obscurely.
    """
    doc = _req(f"https://plc.directory/{did}")
    for svc in doc.get("service", []):
        if svc.get("id", "").endswith("atproto_pds"):
            host = urllib.parse.urlsplit(svc["serviceEndpoint"]).netloc
            return f"did:web:{host}"
    raise LookupError(f"no atproto_pds service in DID doc for {did}")


def service_token(access_jwt: str, lxm: str, aud: str = VIDEO_DID) -> str:
    """A scoped token, narrow in two independent ways.

    `lxm` must name the exact method being called — a token minted for
    uploadBlob is rejected by getUploadLimits and vice versa. And `aud` must
    name whoever actually performs the work, which is not the same service for
    every call: the video host answers getUploadLimits and getJobStatus, but
    the blob write lands on the user's own PDS. One token per call.
    """
    q = urllib.parse.urlencode({"aud": aud, "lxm": lxm, "exp": int(time.time()) + 1800})
    return _req(f"{PDS}/xrpc/com.atproto.server.getServiceAuth?{q}", token=access_jwt)["token"]


def upload(video: Path, did: str, token: str) -> dict:
    """Hand the file to the video service and return the job status.

    Two response shapes have to be accepted. A fresh upload nests the job under
    `jobStatus`; re-uploading identical bytes returns 409 `already_exists` with
    the job flat at the top level. The 409 is a success — the video is on the
    server and processed — so treating it as an error makes the tool fail
    exactly when it has nothing left to do.
    """
    q = urllib.parse.urlencode({"did": did, "name": video.name})
    resp = _req(
        f"{VIDEO}/xrpc/app.bsky.video.uploadVideo?{q}",
        method="POST", token=token, body=video.read_bytes(), ctype="video/mp4",
        ok_codes=(409,),
    )
    return resp.get("jobStatus", resp)


def wait(job_id: str, token: str, *, timeout: float = 600) -> dict:
    deadline = time.time() + timeout
    seen = ""
    while time.time() < deadline:
        st = _req(f"{VIDEO}/xrpc/app.bsky.video.getJobStatus?jobId={job_id}", token=token)["jobStatus"]
        state = st.get("state", "")
        if state != seen:
            print(f"  {state}{'  ' + str(st['progress']) + '%' if st.get('progress') else ''}", flush=True)
            seen = state
        if state == "JOB_STATE_COMPLETED":
            return st
        if "FAILED" in state:
            raise RuntimeError(f"video processing failed: {st.get('error') or st}")
        time.sleep(3)
    raise TimeoutError(f"job {job_id} still {seen} after {timeout}s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--env", required=True, help=".env holding BSKY_IDENTIFIER / BSKY_APP_PASSWORD")
    ap.add_argument("--alt", default="", help="alt text for the video (strongly encouraged)")
    ap.add_argument("--text", default="", help="post text, only used with --create-post")
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=1920)
    ap.add_argument("--create-post", action="store_true",
                    help="actually publish. Off by default: a post is an outbound message.")
    args = ap.parse_args()

    video = Path(args.video).expanduser()
    env = read_env(Path(args.env).expanduser())
    ident = env.get("BSKY_IDENTIFIER") or env.get("BSKY_DM_IDENTIFIER") or env.get("BSKY_HANDLE")
    pw = (env.get("BSKY_APP_PASSWORD") or env.get("BSKY_PASSWORD")
          or env.get("BSKY_DM_APP_PASSWORD"))
    if not ident or not pw:
        print(f"no BSKY_IDENTIFIER / BSKY_APP_PASSWORD in {args.env}", file=sys.stderr)
        return 2

    mb = video.stat().st_size / 1e6
    print(f"video: {video.name}  {mb:.1f} MB  {args.width}x{args.height}")
    print(f"account: {ident}")

    sess = login(ident, pw)
    did = sess["did"]
    print(f"session: {did}")

    limits = _req(f"{VIDEO}/xrpc/app.bsky.video.getUploadLimits",
                  token=service_token(sess["accessJwt"], "app.bsky.video.getUploadLimits"))
    print(f"limits: canUpload={limits.get('canUpload')} "
          f"remainingToday={limits.get('remainingDailyVideos')} "
          f"maxBytes={limits.get('remainingDailyBytes')}")
    if not limits.get("canUpload"):
        print(f"upload refused: {limits.get('message')}", file=sys.stderr)
        return 1

    host = pds_did(did)
    print(f"pds: {host}")
    job = upload(
        video, did,
        service_token(sess["accessJwt"], "com.atproto.repo.uploadBlob", aud=host),
    )
    print(f"job: {job['jobId']}")
    done = wait(job["jobId"], service_token(sess["accessJwt"], "app.bsky.video.getJobStatus"))
    blob = done["blob"]
    print(f"\nblob ref: {blob['ref']['$link']}  ({blob.get('size', '?')} bytes)")

    record = {
        "$type": "app.bsky.feed.post",
        "text": args.text,
        "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "embed": {
            "$type": "app.bsky.embed.video",
            "video": blob,
            "aspectRatio": {"width": args.width, "height": args.height},
            **({"alt": args.alt} if args.alt else {}),
        },
    }

    stash = video.with_suffix(".blob.json")
    stash.write_text(json.dumps({"did": did, "record": record}, indent=2))
    print(f"record staged → {stash}")

    if not args.create_post:
        print("\nNot posting. The blob is uploaded and will stay referencable.")
        print("To publish, re-run the same command with --create-post and --text \"...\"")
        return 0

    if not args.text.strip():
        print("refusing to publish an empty post; pass --text", file=sys.stderr)
        return 2

    res = _req(
        f"{PDS}/xrpc/com.atproto.repo.createRecord",
        method="POST", token=sess["accessJwt"], ctype="application/json",
        body=json.dumps({"repo": did, "collection": "app.bsky.feed.post", "record": record}).encode(),
    )
    rkey = res["uri"].rsplit("/", 1)[-1]
    print(f"posted: https://bsky.app/profile/{ident}/post/{rkey}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
