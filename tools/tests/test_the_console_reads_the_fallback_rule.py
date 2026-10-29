"""The console and the server read one fallback rule (`FRD-310`).

Which kind of request a model may stand in for is decided by the server
(`aira_common.fallback.SERVES`), and the console offers only those models in its pickers
(`features/models/fallback-kinds.ts`). Two copies of one rule drift the day one of them changes —
a console offering a model the server then refuses, or hiding one it would take — so this holds the
console's copy to the server's, and the console's vocabulary of kinds and modes to the server's.
"""

from __future__ import annotations

import re
from pathlib import Path

from aira_common.fallback import SERVES, FallbackKind, FallbackMode

ROOT = Path(__file__).resolve().parents[2]
KINDS_TS = ROOT / "management/frontend/src/app/features/models/fallback-kinds.ts"
TYPES_TS = ROOT / "management/frontend/src/app/core/api/types/pipelines.ts"


def _console_serves() -> dict[str, set[str]]:
    block = re.search(r"export const SERVES[^=]*=\s*\{(.*?)\};", KINDS_TS.read_text(), re.S)
    assert block, "no SERVES table in fallback-kinds.ts — the scan reads nothing"
    return {
        kind: set(re.findall(r"'([a-z_]+)'", values))
        for kind, values in re.findall(r"(\w+):\s*\[([^\]]*)\]", block.group(1))
    }


def _union_words(name: str) -> set[str]:
    found = re.search(rf"export type {name} = ([^;]+);", TYPES_TS.read_text())
    assert found, f"no type {name} in pipelines.ts — the scan reads nothing"
    return set(re.findall(r"'([a-z]+)'", found.group(1)))


def test_the_console_offers_a_model_for_the_kinds_the_server_accepts_it_for() -> None:
    server = {
        str(kind): {str(capability) for capability in serves} for kind, serves in SERVES.items()
    }

    assert _console_serves() == server


def test_the_console_names_the_kinds_and_modes_the_server_knows() -> None:
    assert _union_words("FallbackKind") == {str(kind) for kind in FallbackKind}
    assert _union_words("FallbackMode") == {str(mode) for mode in FallbackMode}
