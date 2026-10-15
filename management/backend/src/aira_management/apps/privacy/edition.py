"""Which edition of the notice this is, and the digest that keeps the date honest (`FRD-625` FR-7).

A notice carries the date it was last changed ("Stand"). A date only a person moves is one that
stays put while the text changes under it, so ``SOURCE_DIGEST`` pins what the notice is made of —
the register and every language's texts — and `test_privacy_notice.py` fails when it no longer
hashes to it. The failure names the new digest; moving ``EDITION`` to the day of the change is the
other half of the same edit.

**What is hashed is what the notice says, not the files' bytes**: each activity and what it stores,
the permissions the notice names, and every language's texts as parsed. A stored column added to an
activity or a changed sentence moves the edition; a comment, a reformatted line or a reason in a
`NOT_PERSONAL_*` table — none of which a reader is shown — asks nobody to read the notice again.

A new edition is also a new `notice.version`, so everybody is asked to read it again.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

from aira_management.apps.privacy.activities import ACTIVITIES, PERSONAL_DATA_PERMISSIONS

TEXTS = Path(__file__).resolve().parent / "texts"

#: The day the notice's content last changed, ISO 8601.
EDITION = "2026-10-15"

#: `source_digest()` as of ``EDITION``.
SOURCE_DIGEST = "285db616e3ec5437f55f279b061be2554be9ad3e530d3612a540a40bb5f15cfa"


def source_digest() -> str:
    """SHA-256 over what the notice is made of, in a canonical form that ignores how it is
    written down."""
    made_of = {
        "activities": [
            [
                activity.key,
                [[str(store.plane), store.table, list(store.columns)] for store in activity.stores],
            ]
            for activity in ACTIVITIES
        ],
        "permissions": [str(permission) for permission in PERSONAL_DATA_PERMISSIONS],
        "texts": {
            path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
            for path in sorted(TEXTS.glob("*.toml"))
        },
    }
    canonical = json.dumps(made_of, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()
