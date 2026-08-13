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
        --env <a local checkout>/.env --alt "..."
"""

from __future__ import annotations

import argparse
import json
import subprocess
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


def probe_dimensions(video: Path, fallback_w: int, fallback_h: int) -> tuple[int, int]:
    """The video's real pixel dimensions.

    aspectRatio goes into a permanent, publicly readable record, and it was
    being stamped from CLI defaults that nobody passes — so any file that was
    not exactly 1080x1920 baked a wrong ratio into something immutable. Falls
    back to the flags only if ffprobe cannot answer.
    """
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(video)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        w, h = (int(v) for v in out.split("x")[:2])
        return w, h
    except (subprocess.CalledProcessError, ValueError, FileNotFoundError):
        print(f"could not probe {video.name}; using {fallback_w}x{fallback_h}", file=sys.stderr)
        return fallback_w, fallback_h


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
    # atproto blesses two DID methods. did:web resolves from the domain itself,
    # and plc.directory knows nothing about those accounts — which is exactly the
    # profile of someone self-hosting a PDS, i.e. a likely user of a publish tool
    # they can read the source of.
    if did.startswith("did:web:"):
        doc = _req(f"https://{did.split(':', 2)[2]}/.well-known/did.json")
    else:
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
        # Both shapes, same as upload(): a 409 carries the job flat at the top
        # level, so indexing ["jobStatus"] crashes on exactly the response this
        # ok_codes was added to salvage.
        resp = _req(
            f"{VIDEO}/xrpc/app.bsky.video.getJobStatus?jobId={job_id}",
            token=token, ok_codes=(409,),
        )
        st = resp.get("jobStatus", resp)
        # Check for a blob BEFORE reading state. Bluesky's own guidance is to look
        # for a BlobRef regardless of whether the job reports success or failure:
        # an already-processed video surfaces here as an error that still carries
        # the usable blob. Reading state first throws away a good upload on retry.
        if st.get("blob"):
            return st
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
    ap.add_argument("--width", type=int, default=1080,
                    help="fallback width if ffprobe cannot read the file")
    ap.add_argument("--height", type=int, default=1920,
                    help="fallback height if ffprobe cannot read the file")
    ap.add_argument("--expect-account",
                    help="account this run must upload as; refuses if --env holds another")
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

    # Identity guard. Three identifier names and three password names bind to
    # whatever --env is passed, and the accounts this project touches are
    # deliberately separate people as far as the network is concerned. One wrong
    # --env uploads under the operator's personal account and creates a
    # cryptographically verifiable link between it and the channel — the exact
    # thing the project's identity separation exists to prevent, and not
    # something you can take back once the blob is on someone's PDS.
    if args.expect_account and args.expect_account != ident:
        print(
            f"--expect-account says {args.expect_account} but {args.env} holds a "
            f"different account. Refusing to upload.",
            file=sys.stderr,
        )
        return 2
    if not args.expect_account:
        print(
            "warning: no --expect-account, so nothing checked which identity this "
            "uploads as. Pass it to bind the run to an account.",
            file=sys.stderr,
        )

    mb = video.stat().st_size / 1e6
    width, height = probe_dimensions(video, args.width, args.height)
    print(f"video: {video.name}  {mb:.1f} MB  {width}x{height}")
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
            "aspectRatio": {"width": width, "height": height},
            **({"alt": args.alt} if args.alt else {}),
        },
    }

    # Beside the video, but not *next to* it in the sense that matters: these
    # mp4s are made to be handed to people, and a sidecar sharing their stem
    # carrying the uploader DID travels with them on a careless drag-select.
    # Under a dotted directory it has to be picked up deliberately.
    stash_dir = video.parent / ".skeetio-records"
    stash_dir.mkdir(exist_ok=True)
    stash = stash_dir / f"{video.stem}.blob.json"
    stash.write_text(json.dumps({"did": did, "record": record}, indent=2))
    stash.chmod(0o600)
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
