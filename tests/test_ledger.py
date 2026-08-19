"""The ledger's job is to make a published video removable, and to stop the bot
answering the same request twice. Both are properties about *absence* — nothing
posted twice, nothing left un-removable — so each one here is tested by making
it fail first.

The two-phase shape is the part that needs holding. One row per attempt breaks
in both directions: mark a request handled on any row and the documented retries
can never fire; count only final rows and a crash between posting and recording
republishes on restart.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

import ledger


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Never touch the real ledger. It is the record of things actually
    published, and a test run that appended to it would be indistinguishable
    from a bot run that did."""
    monkeypatch.setattr(ledger, "STATE", tmp_path)
    monkeypatch.setattr(ledger, "LEDGER", tmp_path / "ledger.jsonl")


REQ = "at://did:plc:asker/app.bsky.feed.post/request"


def _row(outcome: str, **kw) -> ledger.Row:
    base = dict(
        outcome=outcome, request_uri=REQ, requester_did="did:plc:asker",
        source_uri="at://did:plc:author/app.bsky.feed.post/subject",
        source_did="did:plc:author", at=1000.0,
    )
    base.update(kw)
    return ledger.Row(**base)


def test_an_unseen_request_is_not_handled():
    assert not ledger.handled(REQ)


def test_a_claim_holds_the_request_while_it_is_in_flight():
    ledger.append(_row("claimed"))
    assert ledger.handled(REQ, now=1000.0), (
        "a claimed request was offered again while the first attempt was still "
        "running — two pollers would both answer it"
    )


def test_a_stale_claim_is_released():
    """The crash case. Something died holding the claim; the request must become
    available again, and the deterministic rkey is what stops the retry from
    duplicating anything that did get out."""
    ledger.append(_row("claimed"))
    assert not ledger.handled(REQ, now=1000.0 + ledger.STALE_CLAIM_SECS + 1)


@pytest.mark.parametrize("outcome", ledger.TERMINAL)
def test_every_terminal_outcome_settles_the_request(outcome):
    """Including the failures. A permanent refusal that did not settle would be
    retried forever."""
    ledger.append(_row(outcome))
    assert ledger.handled(REQ, now=9e9), f"{outcome!r} did not settle the request"


def test_a_failed_attempt_does_not_block_its_own_retry():
    """The contradiction the two-phase shape exists to avoid: NO_AVATAR is
    documented as 'retry with --generic', so the ledger must not have decided
    the request is finished the moment the first attempt failed."""
    ledger.append(_row("claimed"))
    assert not ledger.handled(REQ, now=1000.0 + ledger.STALE_CLAIM_SECS + 1)


def test_rkey_is_stable_per_request_and_differs_between_requests():
    """This is the actual idempotency guarantee — the ledger is only bookkeeping.
    If it were not stable, a retry would publish a second video."""
    assert ledger.rkey_for(REQ) == ledger.rkey_for(REQ)
    assert ledger.rkey_for(REQ) != ledger.rkey_for(REQ + "x")


# The PDS enforces TID syntax on app.bsky.feed.post record keys. This is the
# regex atproto validates against.
TID = re.compile(r"^[234567abcdefghij][234567abcdefghijklmnopqrstuvwxyz]{12}$")


def test_rkey_is_a_valid_tid():
    """The earlier version of this test checked only the alphabet, which an RFC
    4648 base32 digest satisfies — so it passed while producing keys the server
    rejected better than half the time. The first live post started with a legal
    character by luck; the second did not, and createRecord refused it after the
    video had already been uploaded."""
    assert TID.match(ledger.rkey_for(REQ)), ledger.rkey_for(REQ)


def test_no_request_produces_an_illegal_rkey():
    """Swept rather than sampled, because the failure mode was a *fraction* of
    keys being invalid. One example proves nothing here."""
    bad = [k for k in
           (ledger.rkey_for(f"at://did:plc:x/app.bsky.feed.post/p{i}") for i in range(5000))
           if not TID.match(k)]
    assert not bad, f"{len(bad)} of 5000 keys were not valid TIDs, e.g. {bad[:3]}"


def test_the_tid_check_can_actually_fail():
    """Guards the guard: an RFC 4648 base32 key is the exact thing that used to
    be generated, so the pattern must reject it."""
    assert not TID.match("zzzzzzzzzzzzz")
    assert not TID.match("p2kw2gijnovdk")  # the key the server actually refused


# --- who may take a video down ---------------------------------------------


def test_the_rendered_author_may_remove_their_own():
    row = _row("posted", reply_uri="at://bot/x").__dict__
    assert ledger.may_remove(row, "did:plc:author")


def test_the_requester_may_remove_what_they_asked_for():
    row = _row("posted", reply_uri="at://bot/x").__dict__
    assert ledger.may_remove(row, "did:plc:asker")


def test_a_passer_by_may_not_remove_someone_elses_render():
    """Without this the takedown mechanism takes orders from any mention, and
    anyone can delete anyone's video."""
    row = _row("posted", reply_uri="at://bot/x").__dict__
    assert not ledger.may_remove(row, "did:plc:nobody")


def test_removal_authority_is_keyed_on_did_not_handle():
    """Handles rotate. A takedown that stops working because the person changed
    their username is not a takedown."""
    row = _row("posted", reply_uri="at://bot/x").__dict__
    assert not ledger.may_remove(row, "author.bsky.social")


# --- finding what to remove ------------------------------------------------


def test_live_for_source_lists_what_is_still_up():
    ledger.append(_row("posted", reply_uri="at://bot/a"))
    ledger.append(_row("posted", reply_uri="at://bot/b", request_uri=REQ + "2"))
    live = {r["reply_uri"] for r in ledger.live_for_source("did:plc:author")}
    assert live == {"at://bot/a", "at://bot/b"}


def test_live_for_source_forgets_what_was_already_removed():
    ledger.append(_row("posted", reply_uri="at://bot/a"))
    ledger.append(_row("removed", reply_uri="at://bot/a"))
    assert ledger.live_for_source("did:plc:author") == [], (
        "an already-removed video was offered for removal again — the operator "
        "would be told a delete failed when it had already succeeded"
    )


def test_live_for_source_does_not_leak_between_authors():
    ledger.append(_row("posted", reply_uri="at://bot/a"))
    assert ledger.live_for_source("did:plc:someone-else") == []


def test_a_torn_final_line_does_not_take_the_ledger_out():
    """A kill mid-write leaves a partial line. Refusing to parse the file would
    mean the bot cannot start — and this file is what stops it double-posting,
    so failing closed here fails open where it matters."""
    ledger.append(_row("posted", reply_uri="at://bot/a"))
    with ledger.LEDGER.open("a", encoding="utf-8") as fh:
        fh.write('{"outcome": "clai')
    assert len(ledger.rows()) == 1
    assert ledger.handled(REQ, now=9e9)
