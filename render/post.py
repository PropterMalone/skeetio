# pattern: Imperative Shell
"""Fetch a Bluesky post as a single indivisible record.

This module exists because of a real misattribution. The renderer originally
took `--handle` and `--text` as independent arguments, so one wrong string put
another person's words on screen under someone else's name — in a project whose
entire premise is asking permission before reusing what people wrote.

Text, author, and avatar always travel together here. There is deliberately no
way to supply text and a handle separately: the class of error is removed by
making the wrong thing unrepresentable rather than by being more careful.
"""

from __future__ import annotations

import io
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from PIL import Image

APPVIEW = "https://public.api.bsky.app/xrpc"
UA = {"User-Agent": "skeetio/0.1"}

# https://bsky.app/profile/<handle-or-did>/post/<rkey>
WEB_URL = re.compile(r"bsky\.app/profile/([^/]+)/post/([A-Za-z0-9]+)")
AT_URI = re.compile(r"at://([^/]+)/app\.bsky\.feed\.post/([A-Za-z0-9]+)")


@dataclass(frozen=True)
class Post:
    uri: str
    cid: str
    text: str
    handle: str
    display_name: str
    did: str
    avatar_url: str | None
    created_at: str
    likes: int
    reposts: int
    replies: int
    quotes: int
    reply_parent_uri: str | None
    reply_root: dict | None  # strong ref {uri, cid} of the thread root, if this is a reply

    @property
    def web_url(self) -> str:
        return f"https://bsky.app/profile/{self.handle}/post/{self.uri.rsplit('/', 1)[-1]}"

    @property
    def strong_ref(self) -> dict:
        return {"uri": self.uri, "cid": self.cid}

    @property
    def thread_refs(self) -> dict:
        """The `reply` block needed to answer this post in its own thread.

        A reply carries two strong refs, not one. `parent` is the post being
        answered; `root` is the head of the whole thread, which is only the same
        post when it started the thread. Pointing `root` at a mid-thread post
        detaches the reply — clients thread on root, so it renders as a
        conversation of its own instead of appearing under the original.
        """
        return {"root": self.reply_root or self.strong_ref, "parent": self.strong_ref}

    @property
    def stands_alone(self) -> bool:
        """A reply may depend on its parent for meaning. Not a full portability
        test — deixis and image-dependence still need checking — but the cheap
        structural half, and it is the one the selector must never skip."""
        return self.reply_parent_uri is None


def _get(endpoint: str, **params) -> dict:
    url = f"{APPVIEW}/{endpoint}?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def resolve(ref: str) -> str:
    """Turn a bsky.app URL or at:// URI into a canonical at:// URI."""
    if m := AT_URI.search(ref):
        actor, rkey = m.group(1), m.group(2)
    elif m := WEB_URL.search(ref):
        actor, rkey = m.group(1), m.group(2)
    else:
        raise ValueError(f"not a recognisable post reference: {ref!r}")

    did = actor if actor.startswith("did:") else _get(
        "com.atproto.identity.resolveHandle", handle=actor
    )["did"]
    return f"at://{did}/app.bsky.feed.post/{rkey}"


def fetch(ref: str) -> Post:
    uri = resolve(ref)
    data = _get("app.bsky.feed.getPosts", uris=uri)
    if not data.get("posts"):
        raise LookupError(f"post not found: {ref}")
    p = data["posts"][0]
    a, rec = p["author"], p["record"]
    reply = rec.get("reply") or {}
    parent = reply.get("parent") or {}
    root = reply.get("root") or None
    return Post(
        uri=p["uri"],
        cid=p["cid"],
        text=rec.get("text", ""),
        handle=a["handle"],
        display_name=a.get("displayName") or a["handle"],
        did=a["did"],
        avatar_url=a.get("avatar"),
        created_at=rec.get("createdAt", ""),
        likes=p.get("likeCount", 0),
        reposts=p.get("repostCount", 0),
        replies=p.get("replyCount", 0),
        quotes=p.get("quoteCount", 0),
        reply_parent_uri=parent.get("uri"),
        reply_root={"uri": root["uri"], "cid": root["cid"]} if root else None,
    )


def avatar(post: Post) -> Image.Image:
    if not post.avatar_url:
        raise LookupError(f"@{post.handle} has no avatar set")
    with urllib.request.urlopen(urllib.request.Request(post.avatar_url, headers=UA), timeout=30) as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")
