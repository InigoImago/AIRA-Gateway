"""Making a payload storable, so an audit row is never lost to its own contents.

Losing a value is a smaller failure than losing the row, and replacing it with its name is smaller
still (`FRD-122`): each function here names what it cannot keep instead of raising.
"""

from __future__ import annotations

from typing import Any

#: How deep a payload may be before it is flattened. Above `MAX_JSON_DEPTH`, so a body the gateway
#: accepted is never clipped here; far below the interpreter's recursion limit, because three walks
#: (`strip_attachments`, the redactor, `storable`) run from inside a request that holds a stack.
MAX_STORED_DEPTH = 100

#: What stands in for a subtree too deep to walk, in `storable`'s `<unrepresentable: …>` idiom.
TOO_DEEP = f"<unrepresentable: nested deeper than {MAX_STORED_DEPTH} levels>"

#: Where a payload that is not a JSON object is kept. Every reader indexes the column as a mapping,
#: so a caller's `[1, 2]` or `"text"` is wrapped rather than dropped or allowed to cost the row.
NOT_AN_OBJECT_KEY = "payload"


def within_depth(value: Any, limit: int = MAX_STORED_DEPTH) -> Any:
    """``value`` with anything below ``limit`` levels replaced by :data:`TOO_DEEP`.

    Runs **first**: the other walks recurse without a bound, and a caller — or an upstream answering
    with a thousand nested objects — can reach the recursion limit. `aira_common.nesting` bounds the
    caller's body at the boundary; this bounds the upstream's. Recursion is safe here because it is
    what is bounded: at most ``limit`` frames.
    """
    if limit <= 0:
        return TOO_DEEP
    if isinstance(value, dict):
        return {key: within_depth(item, limit - 1) for key, item in value.items()}
    if isinstance(value, list):
        return [within_depth(item, limit - 1) for item in value]
    return value


def as_object(value: Any) -> dict[str, Any]:
    """``value`` as something a payload column can hold, wrapped if it is not a mapping.

    A check rather than `dict(value)`, which is a coercion: a caller's `[1, 2]` would raise in the
    writer and cost a correctly refused request its row. The writer does not trust an annotation.
    """
    return value if isinstance(value, dict) else {NOT_AN_OBJECT_KEY: value}


def storable(value: Any) -> Any:
    """A payload the database can take, with unencodable values named rather than dropped.

    `json` columns are stricter than Python: `Infinity`, `-Infinity`, `NaN` and a lone surrogate
    are not encodable, and one anywhere costs the whole row. The boundary refuses such a body
    (`ensure_body_is_encodable`); this covers what an upstream answers. **Keys as well as values.**
    """
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return f"<unrepresentable: {value}>"
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "<unrepresentable: unpaired surrogate>"
        return value
    if isinstance(value, dict):
        # `str(key)` first, because a `json` column has string keys; `storable` after it, because
        # that string is subject to the same rule as any other.
        return {str(storable(str(key))): storable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [storable(item) for item in value]
    return value
