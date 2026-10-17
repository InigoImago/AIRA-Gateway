"""Refresh the model reference this release ships (`FRD-508`).

    uv run python tools/model_reference.py            # what would change, nothing written
    uv run python tools/model_reference.py --write    # write the new copy

Reads the public list, keeps what the gateway can serve, in the catalog's own vocabulary, and
writes it beside the code that reads it. Run before a release, so a release's reference is no
older than the release; the diff of `models.json` is the list of what changed in the world.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "management" / "backend" / "src"))

from aira_management.apps.catalog.reference.normalize import drafts  # noqa: E402

#: The list read. Open data, the one OpenCode reads.
SOURCE = "https://models.dev/api.json"

TARGET = (
    ROOT
    / "management"
    / "backend"
    / "src"
    / "aira_management"
    / "apps"
    / "catalog"
    / "reference"
    / "models.json"
)


def build(listing: object, as_of: str) -> dict[str, object]:
    """The file's content: where it came from, when, and the drafts."""
    return {"source": SOURCE, "as_of": as_of, "entries": drafts(listing)}


def _names(document: dict[str, object]) -> set[tuple[str, str]]:
    entries = document.get("entries")
    if not isinstance(entries, list):
        return set()
    return {(str(entry["platform"]), str(entry["name"])) for entry in entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="write the new copy")
    arguments = parser.parse_args(argv)

    response = httpx.get(SOURCE, timeout=30.0, follow_redirects=True)
    response.raise_for_status()
    fresh = build(response.json(), datetime.now(UTC).date().isoformat())
    old = json.loads(TARGET.read_text(encoding="utf-8")) if TARGET.exists() else {}

    added = sorted(_names(fresh) - _names(old))
    gone = sorted(_names(old) - _names(fresh))
    print(f"{len(fresh['entries'])} models ({len(added)} new, {len(gone)} gone)")  # type: ignore[arg-type]
    for platform, name in added:
        print(f"  + {platform}/{name}")
    for platform, name in gone:
        print(f"  - {platform}/{name}")
    if arguments.write:
        TARGET.write_text(json.dumps(fresh, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
