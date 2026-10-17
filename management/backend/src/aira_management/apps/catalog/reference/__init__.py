"""The model reference: drafts of catalog entries from a public model list (`FRD-508`).

    normalize   the list's entries, read into this catalog's declaration
    snapshot    the copy shipped with this release, and the live list where one is configured

Re-exports the names the view and the snapshot tool use.
"""

from __future__ import annotations

from aira_management.apps.catalog.reference.normalize import drafts
from aira_management.apps.catalog.reference.snapshot import (
    SNAPSHOT_PATH,
    Reference,
    current,
    forget,
)

__all__ = ["SNAPSHOT_PATH", "Reference", "current", "drafts", "forget"]
