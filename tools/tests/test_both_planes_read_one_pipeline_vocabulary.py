"""A pipeline's bounds, closed vocabularies and config keys are one set, read by every plane.

`aira_common.pipelines` holds them. Management refuses a configuration outside them where it is
authored; the gateway re-applies the bounds to what it reads and decides on the words; the console
offers the words. Each copy that is not the shared one is compared here, in the direction it can
drift:

- the gateway's bounds and its two failure policies, by value — they are its own literals until it
  imports the shared ones, and a tightened copy on one side is the looser plane on the other;
- the config keys the gateway's steps **read**, against the keys Management **accepts** — a key one
  reads and the other refuses cannot be configured, one accepted and never read is a setting that
  does nothing;
- what the gateway **does** with each word of a vocabulary, so the vocabulary means what the
  console says it means;
- the console's TypeScript unions, by parsing them.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (ROOT / "gateway" / "src", ROOT / "libs" / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aira_common import pipelines  # noqa: E402
from aira_gateway.core.canonical import (  # noqa: E402
    CanonicalMessage,
    CanonicalRequest,
    Role,
)
from aira_gateway.pipeline import config as gateway_config  # noqa: E402
from aira_gateway.pipeline.classifiers import Verdict  # noqa: E402
from aira_gateway.pipeline.engine import steps as gateway_steps  # noqa: E402

STEPS_SOURCE = ROOT / "gateway" / "src" / "aira_gateway" / "pipeline" / "engine" / "steps.py"
CONSOLE_TYPES = ROOT / "management" / "frontend" / "src" / "app" / "core" / "api" / "types"


def test_the_gateway_bounds_are_the_shared_ones() -> None:
    for name in (
        "MAX_STEPS",
        "MAX_FALLBACK_MODELS",
        "MAX_MODEL_LENGTH",
        "MAX_TEXT_LENGTH",
        "MAX_CATEGORIES",
    ):
        assert getattr(gateway_config, name) == getattr(pipelines, name), name


def test_the_gateway_failure_policies_are_the_shared_ones() -> None:
    assert gateway_steps.UNDETERMINED_BLOCKS == pipelines.FailurePolicy.BLOCK
    assert gateway_steps.UNDETERMINED_ALLOWS == pipelines.FailurePolicy.ALLOW


def test_every_key_the_gateway_reads_is_one_management_accepts_and_back() -> None:
    """Compared as sets over all step types: which function reads a key is the gateway's structure,
    and the per-type split is Management's to keep."""
    source = STEPS_SOURCE.read_text()
    read = set(re.findall(r"""config\.get\(\s*["']([a-z_]+)["']""", source))
    accepted = set().union(*pipelines.STEP_CONFIG_KEYS.values())

    assert read, "the pattern matched nothing — this would compare against an empty set"
    assert read == accepted, (
        f"read by the gateway, refused by Management: {sorted(read - accepted)}; "
        f"accepted by Management, read by nothing: {sorted(accepted - read)}"
    )

    category_read = set(re.findall(r"""category\.get\(\s*["']([a-z_]+)["']""", source))
    assert category_read <= pipelines.CATEGORY_KEYS, sorted(category_read - pipelines.CATEGORY_KEYS)


def test_each_action_does_what_its_word_says() -> None:
    blocks = {
        action: gateway_steps._blocks(Verdict.INJECTION, {"action": str(action)})
        for action in pipelines.FilterAction
    }

    assert blocks == {pipelines.FilterAction.BLOCK: True, pipelines.FilterAction.FLAG: False}


def test_each_scope_reads_what_its_word_says() -> None:
    request = CanonicalRequest(
        model="m",
        messages=[
            CanonicalMessage(role=Role.SYSTEM, text="the system"),
            CanonicalMessage(role=Role.USER, text="the user"),
        ],
    )

    read = {
        scope: gateway_steps._scanned_text(request, str(scope)) for scope in pipelines.ScanScope
    }

    assert read[pipelines.ScanScope.USER] == "the user"
    assert "the system" in read[pipelines.ScanScope.SYSTEM_USER]
    assert "the user" in read[pipelines.ScanScope.SYSTEM_USER]


def _console_union(field: str) -> set[str]:
    source = (CONSOLE_TYPES / "pipelines.ts").read_text()
    match = re.search(rf"\b{field}\?: ([^;]+);", source)
    assert match, f"`{field}` is no longer a union of string literals in pipelines.ts"
    return set(re.findall(r"'([a-z_]+)'", match.group(1)))


def test_the_console_offers_exactly_the_shared_words() -> None:
    expected = {
        "mode": pipelines.InjectionMode,
        "action": pipelines.FilterAction,
        "scope": pipelines.ScanScope,
        "on_undetermined": pipelines.FailurePolicy,
        "on_failure": pipelines.FailurePolicy,
    }
    for field, vocabulary in expected.items():
        assert _console_union(field) == {str(word) for word in vocabulary}, field
