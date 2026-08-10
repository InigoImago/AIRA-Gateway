"""Every image this repository names, it also says how to build.

Written after `make showcase` — the one command whose entire job is a first run on a machine that
has never seen this project — tried to **pull** `aira-gateway` and `aira-management`. Neither
exists on any registry. Four services carried `image:` with no `build:`, because they run a second
process out of an image a sibling service builds: the consumer, the relay, the retention job and
the seed.

It failed in the way that is hardest to notice: **only on a machine that had never built the image
before.** Everywhere it had already been built, the tag was lying around in the local store and
compose used it. So it worked for everyone who had run the stack, and broke for exactly the person
the target is for.

The rule is checked in both directions of the thing that matters — a service naming a locally-built
image must declare how to build it — because the next second-process service will be added the same
way this one was, by copying a neighbour and deleting what looked redundant.
"""

from __future__ import annotations

import pathlib

import yaml

COMPOSE = pathlib.Path(__file__).resolve().parents[2] / "deploy" / "compose"

#: Images built here rather than fetched. Anything else (postgres, kafka, ollama…) is pulled on
#: purpose, which is why this is a prefix and not a "no service may pull" rule.
LOCAL_PREFIX = "aira-"


def _services() -> dict[str, dict]:
    services: dict[str, dict] = {}
    for name in ("docker-compose.yml", "docker-compose.apps.yml"):
        document = yaml.safe_load((COMPOSE / name).read_text())
        for service, definition in (document.get("services") or {}).items():
            services[f"{name}:{service}"] = definition or {}
    return services


def test_no_service_asks_a_registry_for_an_image_this_repository_builds() -> None:
    missing = sorted(
        service
        for service, definition in _services().items()
        if str(definition.get("image", "")).startswith(LOCAL_PREFIX) and "build" not in definition
    )

    assert not missing, (
        f"{missing} name an image built here and do not say how to build it — compose will try to "
        "pull it, and it exists on no registry. It works on any machine that has built it before, "
        "which is why this is a test rather than a comment."
    )


def test_the_build_definitions_point_at_files_that_exist() -> None:
    """The other half. A service can declare a build and name a Dockerfile nobody wrote — which
    fails later, during the build, rather than never."""
    root = COMPOSE.parents[1]
    for service, definition in _services().items():
        build = definition.get("build")
        if not isinstance(build, dict):
            continue
        context = (COMPOSE / str(build.get("context", "."))).resolve()
        dockerfile = context / str(build.get("dockerfile", "Dockerfile"))

        assert dockerfile.is_file(), f"{service} builds from {dockerfile}, which does not exist"
        assert root in dockerfile.parents, f"{service} builds from outside the repository"
