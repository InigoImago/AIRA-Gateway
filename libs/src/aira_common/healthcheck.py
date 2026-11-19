"""``python -m aira_common.healthcheck``: one healthcheck for every role of an image (`FRD-153`).

A worker writes a heartbeat (:mod:`aira_common.heartbeat`); a server answers ``/healthz``. The check
reads the heartbeat where there is one and asks the server where there is not, so an image needs one
``HEALTHCHECK`` for all the processes it runs, and nothing in Compose has to know which is which.

It prints one line, healthy or not, and Docker keeps it as the reason:
``docker inspect --format '{{json .State.Health.Log}}' <container>``. Exit status 0 is healthy, 1 is
not.

Standard library only, and the package's root imports nothing: this runs every fifteen seconds in
every container, inside the core the service itself runs on.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from aira_common.heartbeat import (
    LARGEST_BYTES,
    NotAHeartbeat,
    judge,
    parse,
    path_from_environment,
)

#: The port of the process's own ``/healthz``, set by each image: 8001 in the gateway's, 8002 in
#: Management's.
PORT_VARIABLE = "AIRA_HEALTH_PORT"

#: Below Docker's five-second limit for the whole check, so a server that does not answer is
#: reported in words rather than as a check Docker had to kill.
HTTP_TIMEOUT_SECONDS = 4.0

Fetch = Callable[[str], int]


def check(*, path: Path, port: str | None, now: float, fetch: Fetch) -> tuple[bool, str]:
    """Whether this container's process is healthy, and the sentence that says why.

    **A heartbeat that is there decides alone.** A file that cannot be read — missing, a directory,
    not permitted — is no heartbeat, and the server is asked; a file that can be read and holds
    something else is unhealthy, never a reason to ask anybody else.
    """
    try:
        with path.open("rb") as handle:
            raw = handle.read(LARGEST_BYTES + 1)
    except OSError:
        return _ask_the_server(path, port, fetch)
    try:
        reading = parse(raw)
    except NotAHeartbeat as exc:
        return False, f"{path} holds no heartbeat: {exc}"
    return judge(reading, now)


def _ask_the_server(path: Path, port: str | None, fetch: Fetch) -> tuple[bool, str]:
    if not port:
        return False, f"no heartbeat at {path}, and {PORT_VARIABLE} names no port to ask"
    try:
        number = int(port)
    except ValueError:
        number = 0
    if not 0 < number < 65536:
        return False, f"no heartbeat at {path}, and {PORT_VARIABLE}={port!r} is not a port"
    # Only a server's check needs these; a worker's reads a file and pays for no HTTP client.
    import urllib.error

    url = f"http://127.0.0.1:{number}/healthz"
    try:
        status = fetch(url)
    except urllib.error.HTTPError as exc:
        return False, f"{url} answered {exc.code}"
    except OSError as exc:
        # `URLError` keeps the cause in `reason`; a timeout or a refused connection is the error.
        cause = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        return False, f"no heartbeat at {path}, and {url} did not answer: {cause}"
    return 200 <= status < 300, f"{url} answered {status}"


def fetch_status(url: str) -> int:
    """The status ``url`` answers with, asked directly — **never through a proxy** the environment
    names, which would ask somebody else's loopback."""
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return int(response.status)


def main(fetch: Fetch = fetch_status) -> int:
    healthy, said = check(
        path=path_from_environment(),
        port=os.environ.get(PORT_VARIABLE),
        now=time.time(),
        fetch=fetch,
    )
    print(said)
    return 0 if healthy else 1


if __name__ == "__main__":  # pragma: no cover - run as `python -m aira_common.healthcheck`
    sys.exit(main())
