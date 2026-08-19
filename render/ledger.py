# pattern: Imperative Shell
"""What was rendered, for whom, and where it was posted — so it can be undone.

A person whose words and face are in a published video needs a way to make it go
away. That needs two things this project did not have: a record linking a post
back to the request that produced it, and something the author can actually
invoke. This module is the first half.

It is append-only and keyed on DID, never on a handle. Handles rotate when
someone moves to a custom domain, and a takedown that stops working because
somebody changed their username is not a takedown.

Rows are written in two phases, which is the part worth understanding:

    claimed   — written BEFORE the post goes out
    posted / refused / failed — written after, carrying the outcome

One row per attempt does not work in either direction. If any row marks a
request handled, the documented retries (NO_AVATAR → --generic, 4x → backoff)
can never fire, because the first failure poisons the key. If only final rows
count, a crash between createRecord and the append republishes on restart.

The two-phase shape closes both, and it is deliberately *not* the whole
guarantee: a `claimed` row that never got its terminal row means "we do not know
whether that posted", and the honest answer is to go and look rather than guess.
Real idempotency comes from the deterministic rkey in publish.py — the server
refuses the second copy. This file is the record; the rkey is the lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

# Not ~/.cache. The b-roll cache holds things that can be downloaded again; this
# holds the only link between a published video and the request behind it, and
# regenerating it is not possible.
STATE = Path(
    os.environ.get("SKEETIO_STATE") or (Path.home() / ".local" / "state" / "skeetio")
).expanduser()
LEDGER = STATE / "ledger.jsonl"

# How long a claimed row stays authoritative before it is treated as a crash
# rather than as work in progress. A render plus a video upload runs well under
# this; the point is to be longer than the slowest healthy attempt, not to be
# tight.
STALE_CLAIM_SECS = 1800

TERMINAL = ("posted", "refused", "failed", "removed")


def now() -> float:
    return time.time()


# base32-sortable, which is not RFC 4648: the digits lead so that lexical order
# matches numeric order.
_B32 = "234567abcdefghijklmnopqrstuvwxyz"


def rkey_for(request_uri: str) -> str:
    """The record key a reply to `request_uri` is published under.

    Derived rather than random so that publishing the same answer twice collides
    at the server instead of producing two videos in someone's thread.

    It must be a syntactically valid **TID**, because that is what the PDS
    enforces for app.bsky.feed.post — 13 characters of base32-sortable, with the
    first restricted to the low sixteen. An RFC 4648 base32 digest satisfies the
    alphabet and fails the first-character rule better than half the time, which
    is worse than failing always: the first live post happened to start with a
    legal character and the second did not.

    Clearing the top bit is what guarantees the leading character: with the value
    under 2^63 the first five-bit group can only reach 7, which is inside the
    permitted set. No timestamp meaning is claimed — only the shape is required,
    and pretending otherwise would put a false creation time in the key.
    """
    n = int.from_bytes(hashlib.sha256(request_uri.encode("utf-8")).digest()[:8], "big") >> 1
    return "".join(_B32[(n >> (5 * (12 - i))) & 31] for i in range(13))


@dataclass(frozen=True)
class Row:
    outcome: str
    request_uri: str
    requester_did: str
    source_uri: str
    source_did: str
    at: float
    reply_uri: str | None = None
    reply_cid: str | None = None
    clip: str | None = None
    exit_code: int | None = None
    note: str | None = None

    def as_json(self) -> str:
        return json.dumps({k: v for k, v in self.__dict__.items() if v is not None})


def append(row: Row) -> None:
    """Add a row. Opened per call in append mode so two processes interleave
    whole lines rather than truncating each other's."""
    STATE.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(row.as_json() + "\n")
    # The file names who asked for what. It is not a secret, but it is nobody
    # else's business on a shared host.
    LEDGER.chmod(0o600)


def rows() -> list[dict]:
    if not LEDGER.exists():
        return []
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn final line from a kill mid-write. Skipping it is right:
            # the alternative is refusing to start because of one bad row, and
            # this file is the thing that tells us what not to post twice.
            continue
    return out


def handled(request_uri: str, *, now: float | None = None) -> bool:
    """Whether this request is finished with, for the purpose of not repeating it.

    A terminal row settles it. A recent `claimed` row means another attempt is
    in flight — treat it as handled so two pollers do not both answer. A *stale*
    claimed row means something died holding it, and that is deliberately NOT
    reported as handled: the caller should retry, and the deterministic rkey
    stops the retry from duplicating anything that did get out.
    """
    now = time.time() if now is None else now
    for r in rows():
        if r.get("request_uri") != request_uri:
            continue
        if r.get("outcome") in TERMINAL:
            return True
        if r.get("outcome") == "claimed" and now - r.get("at", 0) < STALE_CLAIM_SECS:
            return True
    return False


def attempts(request_uri: str) -> int:
    """How many times this request has been picked up.

    Bounds the retry loop. A stale claim releases a request so a crash does not
    strand it, but that same release would retry a permanently-broken request
    every half hour forever — pairing is deterministic, so a clip archive.org
    will not serve fails identically on every attempt.
    """
    return sum(1 for r in rows()
               if r.get("request_uri") == request_uri and r.get("outcome") == "claimed")


def find_reply(reply_uri: str) -> dict | None:
    """The row that produced a given published post, for the takedown path."""
    for r in reversed(rows()):
        if r.get("reply_uri") == reply_uri and r.get("outcome") == "posted":
            return r
    return None


def may_remove(row: dict, actor_did: str) -> bool:
    """Whether `actor_did` is allowed to take this video down.

    Two people, and only two: the author of the post that was rendered — their
    words, their face, and the reason a takedown path exists at all — and
    whoever asked for it, who can unask. Anyone else passing by cannot delete
    someone else's render, which is the failure mode of letting the mechanism
    take orders from any mention.
    """
    return actor_did in (row.get("source_did"), row.get("requester_did"))


def live_for_source(source_did: str) -> list[dict]:
    """Everything still published that was rendered from a given person's posts.

    Answers "take down everything of mine", which is the request an author is
    most likely to actually make.
    """
    removed = {r.get("reply_uri") for r in rows() if r.get("outcome") == "removed"}
    return [
        r for r in rows()
        if r.get("outcome") == "posted"
        and r.get("source_did") == source_did
        and r.get("reply_uri") not in removed
    ]
