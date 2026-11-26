"""What of a database error may be written to a log line (`FRD-406`).

The text of a database error can carry the values its statement was about, in two places.
SQLAlchemy's dump of the bound parameters is switched off where every engine is built
(`base._ENGINE_OPTIONS`). The other is the database's own ``DETAIL``, which :func:`loggable` cuts —
defined once for both planes in `aira_common.logging.exceptions`, since Management's errors quote
rows the same way.
"""

from __future__ import annotations

from aira_common.logging.exceptions import loggable

__all__ = ["loggable"]
