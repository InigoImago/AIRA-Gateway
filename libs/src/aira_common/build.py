"""What this process is: the release it was built as, from which commit, and when (`FRD-157`).

The version lives in the release's tag and nowhere in the code (`ADR-0029`, `ADR-0030`), and the
images did not carry it: every process said ``0.1.0`` — the number written into the packages
before the first release — in its first line, on every span and metric (``service.version``), so
after an update nothing told which version wrote a line, nor from when the new one ran. The release
job hands the tag, the commit and the time to the image build, which writes them into the image's
environment; a process reads them here. A build of somebody's own carries none of them and says
``0.0.0+local``: it is no release, and a number that looked like one would be the old defect again.

The facts, under the names ``docker inspect`` shows:

- ``AIRA_BUILD_VERSION``, ``AIRA_GIT_COMMIT``, ``AIRA_BUILD_TIME`` — written by every image the
  release job builds;
- ``AIRA_BUILD_NUMBER``, ``AIRA_GIT_BRANCH`` — the predecessor's ``version-info`` asks for them, so
  they are read when somebody sets them; a release writes neither.

**Not settings.** Nothing about them is configured, and a value from a settings file would be a
claim about the image the image did not make. So they are read from the process's environment
alone, and no compose file passes them: ``${AIRA_GIT_COMMIT:-}`` would replace what the image says
with nothing.

    python -m aira_common.build    # the identity, as every line and endpoint states it
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass

#: What a build that is no release says it is: a version below every release, marked as local.
LOCAL_VERSION = "0.0.0+local"

#: The environment a release image carries its identity in.
VERSION_VARIABLE = "AIRA_BUILD_VERSION"
COMMIT_VARIABLE = "AIRA_GIT_COMMIT"
TIME_VARIABLE = "AIRA_BUILD_TIME"
NUMBER_VARIABLE = "AIRA_BUILD_NUMBER"
BRANCH_VARIABLE = "AIRA_GIT_BRANCH"

#: Every name above: what a deployment must never set over the image's own.
IDENTITY_VARIABLES = (
    VERSION_VARIABLE,
    COMMIT_VARIABLE,
    TIME_VARIABLE,
    NUMBER_VARIABLE,
    BRANCH_VARIABLE,
)

#: A tag's spelling of a version: the release job hands over ``v0.1.18``'s ``0.1.18``, and a build
#: by hand may hand over the tag itself.
_TAG = re.compile(r"v(?=\d)")


@dataclass(frozen=True, slots=True)
class Build:
    """One build's identity. ``None`` where the build was not told: a local one, or a fact no
    release writes."""

    version: str
    commit: str | None
    time: str | None
    number: int | None
    branch: str | None

    @property
    def commit_short(self) -> str | None:
        """The commit as Git abbreviates it, seven characters."""
        return self.commit[:7] if self.commit else None


def _text(environ: Mapping[str, str], name: str) -> str | None:
    return environ.get(name, "").strip() or None


def _number(environ: Mapping[str, str], name: str) -> int | None:
    """A build number, or ``None`` for none — and for ``0`` or anything that is not one."""
    try:
        number = int(environ.get(name, "").strip())
    except ValueError:
        return None
    return number if number > 0 else None


def _version(environ: Mapping[str, str]) -> str:
    version = _text(environ, VERSION_VARIABLE)
    if version is None:
        return LOCAL_VERSION
    return version[1:] if _TAG.match(version) else version


def build_of(environ: Mapping[str, str]) -> Build:
    """The identity ``environ`` states, ``0.0.0+local`` for a version it does not."""
    return Build(
        version=_version(environ),
        commit=_text(environ, COMMIT_VARIABLE),
        time=_text(environ, TIME_VARIABLE),
        number=_number(environ, NUMBER_VARIABLE),
        branch=_text(environ, BRANCH_VARIABLE),
    )


def this_build() -> Build:
    """This process's identity: the image's environment, read when asked. No I/O — the liveness
    probe states it."""
    return build_of(os.environ)


def main() -> int:
    """Print the identity as JSON: what the release job compares with the tag it built from."""
    print(json.dumps(asdict(this_build()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
