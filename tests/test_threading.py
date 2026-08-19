"""Where a reply lands, which is a consent property and not a formatting one.

The bot answers a *request* — someone replying to a post to ask for a render.
Which post the answer hangs under decides who sees it, so the reply block is the
part of this program most worth pinning down.

Verified once against a live record rather than reasoned about: `miq.moe` has run
this exact mechanic since 2023, and on a real request its reply carried
`parent` = the request and `root` = the whole thread's root. `thread_refs`
produces the same two refs for the same post. These tests hold that shape without
needing the network.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "render"))

from post import Post

ROOT = "at://did:plc:aaa/app.bsky.feed.post/rootrootroot"
ROOT_CID = "bafyroot"


def _post(uri: str, cid: str, *, parent: str | None = None, root: dict | None = None) -> Post:
    return Post(
        uri=uri, cid=cid, text="words", handle="someone.bsky.social",
        display_name="Someone", did="did:plc:someone", avatar_url=None,
        created_at="2026-08-19T00:00:00Z", likes=0, reposts=0, replies=0, quotes=0,
        reply_parent_uri=parent, reply_root=root,
    )


def test_answering_a_reply_keeps_the_original_thread_root():
    """The case the bot actually hits: someone replies to a post to ask for a
    render, and the answer must hang under *their* request while still belonging
    to the conversation it came from.

    Pointing root at the request instead would detach the answer into a thread of
    its own. That is a real option and it was considered and declined — but it
    must not happen by accident.
    """
    request = _post(
        "at://did:plc:bbb/app.bsky.feed.post/request", "bafyrequest",
        parent=ROOT, root={"uri": ROOT, "cid": ROOT_CID},
    )
    refs = request.thread_refs

    assert refs["parent"] == {"uri": request.uri, "cid": request.cid}, (
        "the answer must hang directly under the request, or it is not an answer "
        "to the person who asked"
    )
    assert refs["root"] == {"uri": ROOT, "cid": ROOT_CID}, (
        "root moved off the original thread — clients thread on root, so the "
        "answer would render as a conversation of its own"
    )


def test_answering_a_thread_starter_makes_it_the_root():
    """A post that started its own thread is its own root. Getting this wrong is
    invisible until someone asks for a render of a top-level post."""
    top = _post("at://did:plc:bbb/app.bsky.feed.post/top", "bafytop")
    assert top.thread_refs == {
        "root": {"uri": top.uri, "cid": top.cid},
        "parent": {"uri": top.uri, "cid": top.cid},
    }


def test_a_reply_block_always_carries_both_cids():
    """A strongRef without its cid is not a strong ref. The parent cid is the one
    at risk: post.fetch keeps the parent's *uri* off the record it fetched but
    drops that parent's cid, so anything building a reply block has to use the
    fetched post's own strong_ref — as thread_refs does — rather than the
    remembered parent."""
    for p in (
        _post("at://did:plc:bbb/app.bsky.feed.post/x", "bafyx"),
        _post("at://did:plc:bbb/app.bsky.feed.post/y", "bafyy",
              parent=ROOT, root={"uri": ROOT, "cid": ROOT_CID}),
    ):
        for side in ("root", "parent"):
            ref = p.thread_refs[side]
            assert ref.get("uri") and ref.get("cid"), f"{side} is missing a uri or cid: {ref}"


def test_stands_alone_distinguishes_a_request_from_a_subject():
    """The bot uses this to tell a request (a reply naming it) from a mention on
    a post with nothing above it, which it cannot serve."""
    assert _post("at://did:plc:bbb/app.bsky.feed.post/top", "bafytop").stands_alone
    assert not _post(
        "at://did:plc:bbb/app.bsky.feed.post/reply", "bafyreply",
        parent=ROOT, root={"uri": ROOT, "cid": ROOT_CID},
    ).stands_alone
