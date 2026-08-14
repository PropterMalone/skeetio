# pattern: Functional Core
"""Exit codes, and what a caller is supposed to do about each one.

These are an API. The renderer is meant to be driven by a bot that takes
requests from people and hands back a video, and for that bot the exit code is
the entire failure channel — it never sees the stderr a human reads. So the
codes have to answer the only question the caller actually has: *do I retry, and
with what?*

The first version could not. `make_video` returned 2 for "this author has no
avatar" and 2 for "this post has no text", which are opposite answers — the
first is fixed by re-running with --generic, the second can never succeed. A bot
mapping code to message would have told half its users the wrong thing.

Codes are grouped so the tens digit alone carries the action:

    0    success
    1x   the request itself cannot be rendered. Never retry; tell the person why.
    2x   the request is fine, this attempt was not. Retry with the named change.
    3x   the operator's configuration is wrong. Alert a human; do not retry.
    4x   the far end said no. Retry later, unchanged.

Codes are never recycled. If a condition stops existing, its number retires with
it — a stale caller mapping an old number must not silently pick up a new
meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

OK = 0

# 1x — permanent, about the request
NO_TEXT = 10
UNRENDERABLE_SCRIPT = 11

# 2x — retryable, about this attempt
NO_AVATAR = 20
CLIP_NOT_PUBLIC_DOMAIN = 21
CLIP_TOO_SHORT = 22

# 3x — operator configuration
BAD_ENV = 30
IDENTITY_MISMATCH = 31
EMPTY_POST_TEXT = 32

# 4x — the far end
UPLOAD_REFUSED = 40


@dataclass(frozen=True)
class Exit:
    code: int
    name: str
    meaning: str
    caller_action: str


# The published contract. README renders this table; a test asserts the two
# agree and that every code a CLI can return appears here — the documentation
# and the behaviour are checked against each other rather than maintained in
# parallel.
CONTRACT: tuple[Exit, ...] = (
    Exit(OK, "OK", "rendered (or uploaded) successfully", "done"),
    Exit(NO_TEXT, "NO_TEXT", "the post has no text — image-only or a bare quote",
         "permanent; tell the requester there is nothing to render"),
    Exit(UNRENDERABLE_SCRIPT, "UNRENDERABLE_SCRIPT",
         "the bundled fonts have no glyphs for this script",
         "permanent for this text; tell the requester the script is unsupported"),
    Exit(NO_AVATAR, "NO_AVATAR", "the author has no avatar set",
         "retry with --generic, which uses no likeness"),
    Exit(CLIP_NOT_PUBLIC_DOMAIN, "CLIP_NOT_PUBLIC_DOMAIN",
         "the named clip's licence is not public domain",
         "retry without --clip to draw a screened one from the library"),
    Exit(CLIP_TOO_SHORT, "CLIP_TOO_SHORT", "the clip is shorter than --dur",
         "retry with a lower --dur, or without --clip"),
    Exit(BAD_ENV, "BAD_ENV", "no usable credentials in the --env file",
         "operator error; alert a human, do not retry"),
    Exit(IDENTITY_MISMATCH, "IDENTITY_MISMATCH",
         "--expect-account does not match the account in --env",
         "operator error; alert a human. Never retry by dropping --expect-account"),
    Exit(EMPTY_POST_TEXT, "EMPTY_POST_TEXT", "--create-post with no --text",
         "operator error; alert a human"),
    Exit(UPLOAD_REFUSED, "UPLOAD_REFUSED", "the video service refused the upload",
         "retry later unchanged; usually a rate or quota limit"),
)

BY_CODE = {e.code: e for e in CONTRACT}


def is_retryable(code: int) -> bool:
    """Whether re-running with the change named in `caller_action` could succeed.

    A 3x is deliberately NOT retryable: the operator's configuration is wrong,
    and a bot that retries around IDENTITY_MISMATCH would be retrying its way
    past the guard that stops it uploading as the wrong person.
    """
    return code // 10 in (2, 4)
