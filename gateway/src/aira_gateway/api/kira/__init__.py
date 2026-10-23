"""The KIRA compatibility surface: the predecessor's wire contract (`FRD-107`, `ADR-0010`)."""

#: Where the surface is mounted, so a migrating consumer changes a base URL and nothing else.
BASE = "/kira/api/external"


def is_kira_path(path: str) -> bool:
    """Whether ``path`` — with any `/uc/<slug>` prefix already stripped — is on the KIRA surface.

    One definition for everything that has to know before a route runs: the body ceiling, the audit
    row it writes, the application's exception handlers, and which credentials are read at all.
    """
    return path.startswith(BASE)
