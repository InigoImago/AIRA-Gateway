"""The console and the server read one fallback rule (`FRD-310`).

Which kind of request a model may stand in for is decided by the server
(`aira_common.fallback.SERVES`), and the console offers only those models in its pickers
(`features/pipelines/fallback-kinds.ts`). Two copies of one rule drift the day one of them changes —
a console offering a model the server then refuses, or hiding one it would take — so this holds the
console's copy to the server's, and the console's kinds and the pipeline field each kind's chain is
saved in to the server's.
"""

from __future__ import annotations

import re
from pathlib import Path

from aira_common.fallback import CHAIN_FIELDS, SERVES, FallbackKind

ROOT = Path(__file__).resolve().parents[2]
KINDS_TS = ROOT / "management/frontend/src/app/features/pipelines/fallback-kinds.ts"
TYPES_TS = ROOT / "management/frontend/src/app/core/api/types/pipelines.ts"


def _console_table(path: Path, name: str) -> dict[str, list[str]]:
    block = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\}}", path.read_text(), re.S)
    assert block, f"no {name} table in {path.name} — the scan reads nothing"
    return {
        kind: re.findall(r"'([a-z_]+)'", values)
        for kind, values in re.findall(r"(\w+):\s*(\[[^\]]*\]|'[a-z_]+')", block.group(1))
    }


def _union_words(name: str) -> set[str]:
    found = re.search(rf"export type {name} = ([^;]+);", TYPES_TS.read_text())
    assert found, f"no type {name} in pipelines.ts — the scan reads nothing"
    return set(re.findall(r"'([a-z]+)'", found.group(1)))


def test_the_console_offers_a_model_for_the_kinds_the_server_accepts_it_for() -> None:
    server = {
        str(kind): {str(capability) for capability in serves} for kind, serves in SERVES.items()
    }

    console = {kind: set(words) for kind, words in _console_table(KINDS_TS, "SERVES").items()}

    assert console == server


def test_the_console_saves_each_kinds_chain_where_the_server_reads_it() -> None:
    """A chain sent under a name the server does not know is dropped by its serializer without a
    word, and the console would show as saved a fallback nobody has."""
    server = {str(kind): [field] for kind, field in CHAIN_FIELDS.items()}

    assert _console_table(TYPES_TS, "CHAIN_FIELDS") == server


def test_the_console_names_the_kinds_the_server_knows() -> None:
    assert _union_words("FallbackKind") == {str(kind) for kind in FallbackKind}
