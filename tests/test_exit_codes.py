"""The exit codes are an API, so they get tested like one.

A bot driving this renderer never sees the stderr a human reads — the exit code
is its entire failure channel. The first version returned 2 for both "this
author has no avatar" (fixed by --generic) and "this post has no text" (never
fixable), so a bot mapping code to message would have told half its users the
wrong thing.

These tests exist to stop three specific regressions:
  - two conditions sharing a code again
  - a code returned by a CLI that the published contract does not document
  - the README's table drifting from the module it documents
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

RENDER = Path(__file__).resolve().parent.parent / "render"
README = Path(__file__).resolve().parent.parent / "README.md"
sys.path.insert(0, str(RENDER))

import exits  # noqa: E402

CLIS = ("make_video.py", "publish.py", "retract.py", "bot.py")


def _returned_exit_names(module: str) -> set[str]:
    """Every `exits.NAME` a module's main() returns, read from the AST.

    Static rather than executed: reaching CLIP_TOO_SHORT for real needs a short
    clip on disk and a network fetch, so a runtime test would cover the cheap
    branches and quietly skip the ones most likely to rot.
    """
    tree = ast.parse((RENDER / module).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Attribute):
            if isinstance(node.value.value, ast.Name) and node.value.value.id == "exits":
                names.add(node.value.attr)
    return names


def test_every_code_is_unique():
    """The bug that started this: two conditions, one code."""
    codes = [e.code for e in exits.CONTRACT]
    dupes = {c for c in codes if codes.count(c) > 1}
    assert not dupes, f"exit codes shared by more than one condition: {dupes}"


def test_no_avatar_and_no_text_are_distinguishable():
    """Named explicitly because these two were the collision, and they are the
    pair a bot most needs to tell apart — one is retryable, one is permanent."""
    assert exits.NO_AVATAR != exits.NO_TEXT
    assert exits.is_retryable(exits.NO_AVATAR)
    assert not exits.is_retryable(exits.NO_TEXT)


@pytest.mark.parametrize("module", CLIS)
def test_every_code_a_cli_returns_is_documented(module):
    returned = _returned_exit_names(module)
    assert returned, f"{module}: found no exits.* returns — has the CLI stopped using the contract?"
    documented = {e.name for e in exits.CONTRACT}
    undocumented = returned - documented
    assert not undocumented, (
        f"{module} returns {undocumented}, which the CONTRACT does not document — "
        f"a caller cannot map it"
    )


@pytest.mark.parametrize("module", CLIS)
def test_no_cli_returns_a_bare_integer(module):
    """A literal `return 2` is how the collision happened in the first place."""
    tree = ast.parse((RENDER / module).read_text())
    bare = [
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, int)
    ]
    assert not bare, (
        f"{module} returns bare exit code(s) {bare}; use a name from render/exits.py "
        f"so the condition is documented and cannot silently collide"
    )


def test_readme_table_matches_the_contract():
    """The table is the published API. Drift here means a bot author is reading
    documentation that no longer describes the program."""
    text = README.read_text()
    for e in exits.CONTRACT:
        row = re.search(rf"^\|\s*`{e.code}`\s*\|\s*`{e.name}`\s*\|", text, re.M)
        assert row, f"README has no exit-code row for {e.code} ({e.name})"
    # And nothing documented that no longer exists.
    for code, name in re.findall(r"^\|\s*`(\d+)`\s*\|\s*`([A-Z_]+)`\s*\|", text, re.M):
        assert int(code) in exits.BY_CODE, f"README documents code {code}, which no longer exists"
        assert exits.BY_CODE[int(code)].name == name, (
            f"README calls code {code} {name!r}; the contract calls it "
            f"{exits.BY_CODE[int(code)].name!r}"
        )


def test_retryability_follows_the_documented_grouping():
    for e in exits.CONTRACT:
        if e.code == exits.OK:
            continue
        group = e.code // 10
        assert group in (1, 2, 3, 4), f"{e.name} has code {e.code}, outside the documented groups"
        assert exits.is_retryable(e.code) == (group in (2, 4)), (
            f"{e.name} is in group {group}x but is_retryable says otherwise"
        )


def test_operator_errors_are_never_retryable():
    """A bot retrying past IDENTITY_MISMATCH would be retrying its way around
    the guard that stops it uploading as the wrong person."""
    assert not exits.is_retryable(exits.IDENTITY_MISMATCH)
    assert not exits.is_retryable(exits.BAD_ENV)
