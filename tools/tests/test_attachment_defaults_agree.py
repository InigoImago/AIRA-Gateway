"""The console shows the gateway's own attachment estimate, not a copy of its own (`FRD-110`).

An empty token box in the model editor means "the gateway reserves its built-in estimate", and the
box's placeholder says which number that is. Two tables, one in each language; this holds them to
each other, so the number on screen is the number the gateway reserves.
"""

from __future__ import annotations

import re
from pathlib import Path

from aira_gateway.catalog import DEFAULT_ATTACHMENT_TOKENS, FALLBACK_ATTACHMENT_TOKENS

EDITOR = (
    Path(__file__).resolve().parents[2]
    / "management/frontend/src/app/features/models/model-editor.ts"
)


def _console_table() -> tuple[dict[str, int], int]:
    source = EDITOR.read_text(encoding="utf-8")
    block = re.search(r"DEFAULT_ATTACHMENT_TOKENS[^=]*=\s*\{(.*?)\};", source, re.S)
    assert block, "the console's table is gone"
    entries = {
        key.strip("'\""): int(value)
        for key, value in re.findall(r"([\w'/\"-]+):\s*(\d+)", block.group(1))
    }
    fallback = re.search(r"FALLBACK_ATTACHMENT_TOKENS = (\d+);", source)
    assert fallback, "the console's fallback is gone"
    return entries, int(fallback.group(1))


def test_the_console_shows_the_gateways_estimate() -> None:
    entries, fallback = _console_table()

    assert entries == DEFAULT_ATTACHMENT_TOKENS
    assert fallback == FALLBACK_ATTACHMENT_TOKENS
