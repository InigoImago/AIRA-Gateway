"""Credentials out of every URL that is logged, traced or exported (`ADR-0007`).

A credential hides in two places in a URL: a query parameter (the Gemini protocol's ``?key=``) and
the authority (``redis://user:password@host``). Every reader goes through these functions, so no
reader redacts a shorter list than another.
"""

from __future__ import annotations

import logging

#: What a credential is replaced with.
REDACTED = "REDACTED"

#: Query parameters that may carry a credential. They must never reach a span attribute, a log line
#: or an exported trace.
SENSITIVE_QUERY_PARAMS = frozenset(
    {"key", "api_key", "apikey", "access_token", "token", "password"}
)

#: The loggers that emit a request line. Named rather than filtered at the root, because a root
#: filter does not run for a child logger with its own handler — which is how uvicorn is configured.
ACCESS_LOGGERS = ("uvicorn.access", "gunicorn.access", "django.server")


def redact_query_string(query: str) -> str:
    """Return ``query`` with the values of credential-bearing parameters replaced.

    Order and unknown parameters are preserved, so the result stays useful for debugging
    (e.g. ``alt=sse&key=REDACTED``).
    """
    if not query:
        return query
    parts: list[str] = []
    for pair in query.split("&"):
        name, separator, _value = pair.partition("=")
        if separator and name.lower() in SENSITIVE_QUERY_PARAMS:
            parts.append(f"{name}={REDACTED}")
        else:
            parts.append(pair)
    return "&".join(parts)


def redact_url_query(value: str) -> str:
    """Return ``value`` — a URL or a request line — with credential-bearing parameters replaced.

    The one definition for the access log, the inbound server span
    (`gateway/app.redact_span_query`) and the outbound client span
    (`gateway/telemetry.redact_client_span_url`, `FRD-117` FR-5).
    """
    if "?" not in value:
        return value
    path, _, query = value.partition("?")
    return f"{path}?{redact_query_string(query)}"


def redact_url_credentials(value: str) -> str:
    """Replace a ``user:password@`` in a URL's authority with ``user:REDACTED@``.

    Split on the `scheme://` marker rather than with `urlsplit`, because some readers are handed a
    request line (`GET /v1/x?y HTTP/1.1`) rather than a URL. No marker, nothing to do.
    """
    marker = "://"
    if marker not in value:
        return value
    scheme, _, rest = value.partition(marker)
    authority, slash, path = rest.partition("/")
    if "@" not in authority:
        return value
    userinfo, _, host = authority.rpartition("@")
    # The user stays: which account we connected as is what integration debugging asks.
    user = userinfo.partition(":")[0]
    return f"{scheme}{marker}{user}:{REDACTED}@{host}{slash}{path}"


def redact_target(value: str) -> str:
    """Both redactions, for an address about to be written to a log line.

    `integration_debug` is handed addresses from six clients that between them use both hiding
    places, so no call site has to know which kind it holds.
    """
    return redact_url_credentials(redact_url_query(value))


def _redact_arg(value: object) -> object:
    return redact_url_query(value) if isinstance(value, str) else value


class AccessLogRedaction(logging.Filter):
    """Keep credentials out of the web server's access log, not only out of spans.

    Rewrites the record's arguments rather than the message, because uvicorn formats the line
    itself after filters run. Every string argument is redacted, not the one positional index
    uvicorn uses today — a filter pinned to `args[2]` is silently disabled by an upgrade.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(_redact_arg(arg) for arg in args)
        elif isinstance(args, dict):
            record.args = {key: _redact_arg(value) for key, value in args.items()}
        return True


def install_access_log_redaction() -> None:
    """Attach :class:`AccessLogRedaction` to the access loggers, once."""
    for name in ACCESS_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(existing, AccessLogRedaction) for existing in logger.filters):
            logger.addFilter(AccessLogRedaction())
