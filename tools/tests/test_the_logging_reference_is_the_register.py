"""`docs/LOGGING.md`'s table of fields is the register the code writes lines by (`FRD-631` FR-7).

Somebody who maps the lines into a SIEM reads the document, and a line is written by the register;
a second copy written by hand is the copy that goes stale. The table is generated, and this fails
when it was not regenerated after the register changed.
"""

from __future__ import annotations

import log_fields_reference


def test_the_documents_table_is_the_register_as_it_stands() -> None:
    document = log_fields_reference.DOCUMENT.read_text(encoding="utf-8")
    assert log_fields_reference.rendered(document) == document, (
        "docs/LOGGING.md's table of fields differs from aira_common/logging/fields.py: run "
        "`uv run python tools/log_fields_reference.py --write`"
    )


def test_every_registered_field_is_in_the_document() -> None:
    from aira_common.logging import FIELDS

    document = log_fields_reference.DOCUMENT.read_text(encoding="utf-8")
    assert [name for name in FIELDS if f"| `{name}` |" not in document] == []
