# pattern: Imperative Shell
"""Take a published video down.

The route a person actually uses is a reply to the video naming the bot — see
bot.py. This is the operator's backstop, for when the bot is not running, or the
request arrives by some other channel, or the record predates the ledger.

    python3 render/retract.py --env .env --reply-to at://…/post/abc
    python3 render/retract.py --env .env --source someone.bsky.social --dry-run

`--source` takes a handle and immediately resolves it to a DID, because the
ledger is keyed on DIDs: a takedown that misses because somebody moved to a
custom domain is not a takedown.

Deleting is not reversible, so `--dry-run` lists and stops, and a run with no
matching rows says so rather than reporting success over an empty set.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import exits  # noqa: E402
import ledger  # noqa: E402
import post as P  # noqa: E402
from publish import PDS, ApiError, _req, login, read_env  # noqa: E402


def delete_record(uri: str, token: str) -> None:
    did, collection, rkey = uri.removeprefix("at://").split("/", 2)
    _req(
        f"{PDS}/xrpc/com.atproto.repo.deleteRecord",
        method="POST", token=token, ctype="application/json",
        body=json.dumps({"repo": did, "collection": collection, "rkey": rkey}).encode(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help=".env holding the bot's credentials")
    ap.add_argument("--reply-to", help="at:// URI of one published video to remove")
    ap.add_argument("--source",
                    help="handle or DID whose renders should all come down — the "
                         "'take down everything of mine' case")
    ap.add_argument("--expect-account",
                    help="account this run must act as; refuses if --env holds another")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be deleted and stop")
    args = ap.parse_args()

    if not args.reply_to and not args.source:
        print("pass --reply-to or --source", file=sys.stderr)
        return exits.EMPTY_POST_TEXT

    if args.reply_to:
        row = ledger.find_reply(args.reply_to)
        targets = [row] if row else []
        if not targets:
            # Deliberately not an error: a video the ledger never recorded still
            # needs taking down, and refusing here would send the operator to
            # the web client to do it by hand.
            print(f"no ledger row for {args.reply_to}; deleting it anyway", file=sys.stderr)
            targets = [{"reply_uri": args.reply_to, "source_did": None, "requester_did": None}]
    else:
        try:
            did = args.source if args.source.startswith("did:") else P.resolve_actor(args.source)
        except (ApiError, urllib.error.URLError, TimeoutError, OSError, KeyError, LookupError) as e:
            print(f"could not resolve {args.source}: {e}", file=sys.stderr)
            return exits.FETCH_FAILED
        targets = ledger.live_for_source(did)
        print(f"{args.source} → {did}")

    if not targets:
        print("nothing to remove")
        return exits.OK

    for t in targets:
        print(f"  {t['reply_uri']}  (rendered from {t.get('source_uri', '?')})")
    if args.dry_run:
        print(f"\n--dry-run: {len(targets)} record(s) left alone")
        return exits.OK

    env = read_env(Path(args.env).expanduser())
    ident = env.get("BSKY_IDENTIFIER") or env.get("BSKY_HANDLE")
    pw = env.get("BSKY_APP_PASSWORD") or env.get("BSKY_PASSWORD")
    if not ident or not pw:
        print(f"no credentials in {args.env}", file=sys.stderr)
        return exits.BAD_ENV
    if args.expect_account and args.expect_account != ident:
        print(f"--expect-account says {args.expect_account}, {args.env} holds another",
              file=sys.stderr)
        return exits.IDENTITY_MISMATCH

    try:
        sess = login(ident, pw)
    except (ApiError, urllib.error.URLError, TimeoutError, OSError, KeyError) as e:
        print(f"could not log in: {e}", file=sys.stderr)
        return exits.FETCH_FAILED

    for t in targets:
        uri = t["reply_uri"]
        try:
            delete_record(uri, sess["accessJwt"])
        except ApiError as e:
            # A record that is already gone is the outcome we wanted. Anything
            # else stops the run rather than continuing down the list — if the
            # first delete failed for a reason that is not "already deleted",
            # the rest will fail the same way.
            #
            # ApiError and not HTTPError: _req raises the former, so this whole
            # branch was unreachable and a bulk takedown crashed on the first
            # already-removed record, leaving every later one live.
            if e.code not in (400, 404):
                print(f"{uri}: delete refused ({e.code})", file=sys.stderr)
                return exits.UPLOAD_REFUSED
            print(f"{uri}: already gone")
        ledger.append(ledger.Row(
            outcome="removed",
            request_uri=t.get("request_uri", ""),
            requester_did=t.get("requester_did") or "",
            source_uri=t.get("source_uri") or "",
            source_did=t.get("source_did") or "",
            at=ledger.now(),
            reply_uri=uri,
            note="retract.py",
        ))
        print(f"removed {uri}")
    return exits.OK


if __name__ == "__main__":
    raise SystemExit(main())
