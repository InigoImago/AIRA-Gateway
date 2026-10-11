"""A published version can be *run*, and running it cannot quietly become a build (`FRD-138`).

`FRD-137` put three images in a registry at `vX.Y.Z`. Nothing consumed them: every `image:` in
`docker-compose.apps.yml` is paired with a `build:`, because the unqualified name `aira-gateway` is
on no registry and a service with an image and no build is one Compose tries to pull. So a
deployment still built from a checkout, and "which AIRA is running" still had no answer.

Two overlays close that, and each has a property that is **invisible when it breaks**:

`docker-compose.release.yml` re-points the three images and **removes** their `build:`. Leave one
build in place and `docker compose up` builds the missing image instead of pulling it — the stack
comes up healthy, serves requests, and runs this checkout's code under a version number it took
from the registry. Nothing fails. The one thing a version number is for is gone.

`docker-compose.external.yml` makes every address the deployment must supply **required**. The
defaults it overrides are the container names of the stack this repository ships (`postgres`,
`kafka:9092`, `keycloak:8080/realms/aira`): right there, wrong here, and wrong in the way that
costs an afternoon — `up` reports success and the failure is a connection error several screens
into a container log.

Parsed rather than grepped, through `compose_files.load`, because a search for `build` passes on a
`build` in a comment and a search for `image:` cannot tell you *which* services were missed.
"""

from __future__ import annotations

import pathlib
import re

import compose_files
import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github/workflows/ci.yml"

#: Images this repository builds, as `docker-compose.apps.yml` names them before an overlay.
LOCAL_PREFIX = "aira-"

#: What the release overlay must *not* leave to a default. `latest` moves, and a deployment that
#: cannot say which version it runs is the state `FRD-137` was built to end.
VERSION_VARIABLE = "AIRA_VERSION"


def _apps() -> dict[str, dict]:
    return compose_files.load(compose_files.APPS)["services"]


def _release() -> dict[str, dict]:
    return compose_files.load(compose_files.RELEASE_ONLY)["services"]


def _external() -> dict[str, dict]:
    return compose_files.load(compose_files.EXTERNAL_ONLY)["services"]


def _built_here() -> dict[str, str]:
    """`service -> image` for every service whose image this repository builds."""
    return {
        name: str(service["image"])
        for name, service in _apps().items()
        if str(service.get("image", "")).startswith(LOCAL_PREFIX)
    }


# --- the release overlay: published images, and no way back to a build -------------------------


def test_every_image_built_here_is_re_pointed_at_the_registry() -> None:
    """**Completeness, which is the only property that matters here.** One service left on
    `aira-gateway:dev` is a stack where seven containers are `v0.1.1` and one is whatever was last
    built on that machine — and because they share a database and a schema, the odd one out is a
    migration or a consumer, not something that shows up as a version mismatch anywhere a reader
    looks.
    """
    missing = sorted(set(_built_here()) - set(_release()))

    assert not missing, (
        f"{missing} build an image here and are not re-pointed by docker-compose.release.yml, so "
        "`make up-apps RELEASE=vX.Y.Z` runs them from this checkout while the rest of the stack "
        "comes from the registry"
    )


def test_nothing_in_the_release_overlay_can_still_be_built() -> None:
    """`build: !reset null` **removes** the inherited key. An overlay that merely overrides it
    keeps a build, and Compose builds a missing image rather than pulling it: the stack comes up,
    and it is this checkout under the registry's version number.

    Asserted as *present and emptied*, because the two states are different statements. A service
    with no `build` key at all in this file has inherited `docker-compose.apps.yml`'s.
    """
    for name, service in _release().items():
        assert "build" in service, (
            f"{name} does not reset its build, so it keeps the one from docker-compose.apps.yml "
            "and `docker compose up` builds it when the image is not already local"
        )
        assert service["build"] is None, f"{name} overrides its build instead of removing it"


def test_the_version_must_be_given_and_never_defaults_to_a_moving_tag() -> None:
    """Compose's `${VAR:?message}`: unset is a refusal with a message, before a container starts.

    A default would be the wrong kind of convenience. `:-latest` moves under a restart, and
    `:-dev` would silently be back to building. `latest` is still in the registry for somebody
    trying AIRA out — typing it is then a deliberate act, which is the difference.
    """
    for name, service in _release().items():
        image = str(service["image"])

        assert f"${{{VERSION_VARIABLE}:?" in image, (
            f"{name} does not require {VERSION_VARIABLE}: {image}. A version with a default is a "
            "deployment that cannot say what it runs"
        )
        assert ":-latest" not in image and ":-dev" not in image, (
            f"{name} falls back to a moving tag: {image}"
        )


def test_the_registry_is_overridable_and_its_default_is_a_path_ghcr_accepts() -> None:
    """A fork publishes to its own namespace, so the prefix is a variable. Its default is
    lower-cased deliberately: **GHCR rejects an upper-case path**, and this owner's name has
    capitals — the kind of detail that fails at the push, after a build."""
    for name, service in _release().items():
        image = str(service["image"])
        prefix, _, _ = image.rpartition("/aira-")

        assert prefix.startswith("${AIRA_IMAGE_REGISTRY:-"), (
            f"{name} hard-codes its registry: {image}"
        )
        default = prefix.removeprefix("${AIRA_IMAGE_REGISTRY:-").rstrip("}")
        assert default == default.lower(), (
            f"{name}'s default registry path has upper case in it ({default}); GHCR refuses it"
        )


def test_the_three_image_names_are_the_three_the_release_job_publishes() -> None:
    """**Both halves of one fact, in two files.** The workflow pushes three names and this overlay
    pulls three names; nothing but this test relates them. Rename an image in the workflow and
    every release after it publishes something no deployment file asks for — and the deployment
    keeps working, from the copy already on the machine, until a new host joins.
    """
    published = set(re.findall(r"ghcr\.io/[^/\s]+/(aira-[a-z]+):", WORKFLOW.read_text()))
    run = {
        str(service["image"]).rpartition("/")[2].partition(":")[0]
        for service in _release().values()
    }

    assert published == run, (
        f"the release job publishes {sorted(published)} and the overlay runs {sorted(run)}"
    )


def test_the_demo_seed_is_re_pointed_too_although_nothing_in_this_file_declares_it() -> None:
    """**`extends:` does not see an overlay.** `management-seed` extends `management-migrate`
    *from `docker-compose.apps.yml` on disk*, not from the merged `-f` chain — so re-pointing
    `management-migrate` leaves the seed on `aira-management:dev`, and `make showcase RELEASE=…`
    runs `manage.py seed_demo` from this checkout against a database three published images own.

    The profile is the other half: without it, naming the service here *declares* one — an image
    with no command, started by every `up` that does not layer the showcase on.
    """
    seed = _release().get("management-seed")

    assert seed is not None, "the demo's seed is not re-pointed, so it is built from this checkout"
    assert seed["profiles"] == ["demo"], (
        "the seed entry is not confined to the demo profile, so it becomes a service of its own in "
        "every composition that does not include docker-compose.showcase.yml"
    )
    # **And the order of the two files, which decides who wins.** Compose merges overlays in the
    # order they are given. `docker-compose.showcase.yml`'s own `management-seed` resolves its
    # `extends` to `aira-management:dev` *with* a build — so layered **after** the release overlay
    # it would win, and the seed would be the one container built from source again.
    full = next(
        line for line in MAKEFILE.read_text().splitlines() if line.startswith("COMPOSE_FULL :=")
    )
    assert full.index("$(SHOWCASE_F)") < full.index("$(RELEASE_F)"), (
        f"{full.strip()}: the release overlay is layered before the showcase, so the showcase's "
        "`extends`-resolved image for the seed overrides it"
    )
    assert compose_files.SHOWCASE_RELEASE.index(
        compose_files.SHOWCASE_ONLY
    ) < compose_files.SHOWCASE_RELEASE.index(compose_files.RELEASE_ONLY), (
        "compose_files.SHOWCASE_RELEASE has the same ordering defect"
    )


def test_a_published_version_is_pulled_rather_than_assumed_present() -> None:
    """`pull_policy` is what Compose does when the image is not local. `build` is a legal value
    for it and would be this file undoing itself."""
    for name, service in _release().items():
        policy = str(service["pull_policy"])

        assert policy.startswith("${AIRA_PULL_POLICY:-"), f"{name} pins its pull policy"
        assert "build" not in policy, f"{name}'s pull policy can fall back to building"


# --- the external overlay: infrastructure that is not ours -------------------------------------


def test_the_external_overlay_starts_no_infrastructure() -> None:
    """The point of the file. A Postgres started here is a second database beside the one the
    deployment was pointed at, and both would be healthy."""
    infrastructure = set(compose_files.load(compose_files.INFRA)["services"])
    started = set(_external()) & infrastructure

    assert not started, f"{sorted(started)} are the deployment's, and this file starts them"


def test_no_service_is_left_depending_on_a_container_that_is_not_started() -> None:
    """**Compose refuses the whole composition** for a dependency on an undefined service, so this
    one fails loudly rather than quietly — and it fails for *every* integrated deployment, at the
    first `up`, which is why the merge is checked here instead.

    Merged the way Compose merges: the overlay's `depends_on` replaces the inherited one where it
    is given (`!override`, `!reset`), and the inherited one stands where it is not. The services
    that remain are the applications alone.
    """
    # **The tag, read as text, because parsing erases it.** A re-declared `depends_on:` without
    # `!override` *merges* into the inherited one, so `keycloak` and `redis` survive — and the
    # merge below cannot see the difference, since both spellings load as the same mapping. This is
    # the half of the property that only the file itself can answer.
    for line in compose_files.EXTERNAL_ONLY.read_text().splitlines():
        if line.strip().startswith("depends_on:"):
            assert "!override" in line or "!reset" in line, (
                f"{line.strip()!r} merges into the dependencies it was meant to replace, so the "
                "ones naming containers this composition does not start are still there"
            )

    apps, external = _apps(), _external()
    available = set(apps) - set(compose_files.load(compose_files.INFRA)["services"])
    # The one-shots that are no longer started are not available to depend on either.
    available -= {name for name, service in external.items() if service.get("profiles")}

    for name in available:
        merged = external.get(name, {})
        inherited = apps[name].get("depends_on")
        # `.get` with the inherited value as the default is exactly the merge: a key reset to
        # `None` is *present*, and present-and-empty means the dependency is gone.
        dependencies = merged.get("depends_on", inherited)
        for dependency in dependencies or ():
            assert dependency in available, (
                f"{name} depends on {dependency}, which this composition does not start — Compose "
                "refuses the whole file for that, so no integrated deployment starts at all"
            )


@pytest.mark.parametrize(
    "variable",
    [
        "AIRA_POSTGRES_HOST",
        "AIRA_KAFKA_BOOTSTRAP_SERVERS",
        "AIRA_OIDC_ISSUER",
        "AIRA_OIDC_JWKS_URI",
    ],
)
def test_an_address_that_cannot_be_guessed_is_required(variable: str) -> None:
    """Each of these defaults, one file down, to the container name of a stack that is not running.
    `${VAR:?message}` turns the afternoon that costs into a refusal naming the variable.

    **Checked per occurrence, not per file.** Both planes need the same four addresses, so the
    first draft — *"is `${AIRA_OIDC_JWKS_URI:?` in this file?"* — was satisfied by the control
    plane's copy while the gateway's had been turned back into a default. Its mutation survived.
    The same shape as `PB18`, two features apart: a substring is present as long as **one** of the
    places that needs it still has it.
    """
    used = re.findall(rf"\$\{{{variable}([^}}]*)", compose_files.EXTERNAL_ONLY.read_text())

    assert used, f"{variable} is not asked about at all"
    for how in used:
        assert how.startswith(":?"), (
            f"one of the {len(used)} uses of {variable} is `${{{variable}{how}}}`, so that plane "
            "silently keeps docker-compose.apps.yml's default — the name of a container it does "
            "not run"
        )


@pytest.mark.parametrize("variable", ["AIRA_REDIS_URL", "VAULT_ADDR"])
def test_a_deliberate_none_is_a_valid_answer_for_the_two_that_have_one(variable: str) -> None:
    """`${VAR?…}` without the colon: an error when **unset**, accepted when deliberately **empty**.

    Both of these have a real "none". No Redis means per-process rate limits and the older budget
    path — reported by `/readyz` as `degraded: true`, a documented state rather than a failure, and
    one that must be *chosen* because N replicas then allow N times each limit. No Vault means
    every credential comes from the environment files instead, which the loader does silently.

    Required-and-non-empty would make a lie the only way to start; not asked at all is how both
    end up unanswered.

    Per occurrence, for the same reason as the test above: one plane keeping the right spelling
    says nothing about the other.
    """
    used = re.findall(rf"\$\{{{variable}([^}}]*)", compose_files.EXTERNAL_ONLY.read_text())

    assert used, f"{variable} is not asked about at all"
    for how in used:
        assert how.startswith("?"), (
            f"`${{{variable}{how}}}` must be answerable with nothing — an installation that "
            "deliberately runs without it cannot be made to invent a value, and one that forgot "
            "it must still be asked"
        )


def test_the_applications_network_is_the_one_the_addresses_resolve_on() -> None:
    """`docker-compose.apps.yml` says `networks: [aira]` and defines nothing — the infrastructure
    file owns that network, and this composition does not include it. So the definition is
    duplicated here, and a duplicate is only safe while it is checked: a second expression for the
    name would give the applications a bridge of their own, on which every name the deployment
    supplied resolves somewhere else or nowhere.
    """
    infra = compose_files.load(compose_files.INFRA)
    external = compose_files.load(compose_files.EXTERNAL_ONLY)

    assert external["networks"]["aira"]["name"] == infra["networks"]["aira"]["name"], (
        f"the two definitions of the `aira` network disagree: {external['networks']} here, "
        f"{infra['networks']} in docker-compose.yml"
    )
    # **And the project name, which Compose takes from a *directory* when no file declares one.**
    # The integrated composition does not include `docker-compose.yml`, where the bundled stack's
    # name is declared — so without its own it is the project `compose`. Nothing about the
    # containers would look wrong, because their names are explicit; what breaks is `make down`,
    # `make ps` and `make logs`, which all name the stack and would then report on a project these
    # containers are not in. Found by reading what `docker compose config` said the name was.
    assert external["name"] == infra["name"], (
        f"the integrated composition is the project {external.get('name')!r} while the stack is "
        f"{infra['name']!r}, so every target that names the stack misses these containers"
    )


def test_creating_the_topics_is_opt_in_against_infrastructure_that_is_not_ours() -> None:
    """A broker reached over SASL or TLS needs a client properties file this one-shot cannot
    assemble, so against a secured cluster it fails — and two services wait on
    `service_completed_successfully` and never start. Behind a profile it is available to whoever
    may create topics on a PLAINTEXT broker and absent for everybody else."""
    assert _external()["kafka-topics"].get("profiles") == ["create-topics"]


def test_the_topic_creator_uses_the_broker_the_applications_were_configured_with() -> None:
    """It said `--bootstrap-server kafka:9092`, which is the same value as the default in the
    bundled stack and a different one everywhere else. Pointed at an external broker, the
    applications moved and the container that creates the topics they read did not."""
    topics = _apps()["kafka-topics"]
    command = " ".join(topics["command"])

    assert "AIRA_KAFKA_BOOTSTRAP_SERVERS" in topics["environment"]
    assert "--bootstrap-server kafka:9092" not in command, (
        "the topic creator has its own idea of where the broker is"
    )
    assert "AIRA_KAFKA_BOOTSTRAP_SERVERS" in command


# --- the overlays leave the rest of the model alone --------------------------------------------


def test_neither_overlay_declares_a_service_the_core_files_do_not_define() -> None:
    """**Why these two files are not in `compose_files.ALL`.** That list is every file that
    *defines* a service, and thirteen checks read it to ask "does any container receive this
    variable". The overlays define none: every entry re-points one that exists. A new service
    added here would be invisible to all of them — and would also be a service only one deployment
    shape has, which is its own problem.
    """
    defined = set(_apps()) | set(compose_files.load(compose_files.SHOWCASE_ONLY)["services"])

    for overlay, services in (("release", _release()), ("external", _external())):
        new = sorted(set(services) - defined)
        assert not new, (
            f"docker-compose.{overlay}.yml declares {new}, which no core file defines. Either add "
            "the service to docker-compose.apps.yml and register the file in compose_files.ALL, "
            "or do not introduce a service that only one deployment shape has"
        )


def test_the_makefile_switch_changes_the_file_and_the_flag_together() -> None:
    """**One switch, because they are one decision.** `RELEASE=vX.Y.Z` adds the overlay *and*
    drops the `--build` that `up-apps` and `showcase` pass by default. Either half alone is a
    contradiction: the overlay without the flag change means `--build` against a service whose
    build was removed, and the flag change without the overlay means a stack that is not rebuilt
    and not published either.
    """
    body = MAKEFILE.read_text()
    switch = body.partition("ifdef RELEASE")[2].partition("endif")[0]

    assert "docker-compose.release.yml" in switch, "RELEASE does not add the overlay"
    assert "BUILD_FLAG :=" in switch, "RELEASE does not drop --build"
    assert "export AIRA_VERSION := $(RELEASE)" in switch, (
        "the version is not exported, so Compose refuses the composition it was just handed"
    )
    assert "up -d --build" not in body, (
        "an up target still hard-codes --build, so RELEASE= rebuilds it from this checkout"
    )


def test_the_integrated_target_starts_the_applications_and_not_the_infrastructure() -> None:
    """The composition is what makes `up-integrated` mean anything: with the infrastructure file in
    it, the target would start a Postgres beside the one it was pointed at."""
    composition = next(
        line for line in MAKEFILE.read_text().splitlines() if line.startswith("COMPOSE_INTEGRATED")
    )

    assert "$(APPS_F)" in composition and "$(EXTERNAL_F)" in composition
    assert "$(INFRA_F)" not in composition, (
        "make up-integrated starts this repository's infrastructure as well as the deployment's"
    )


def test_the_published_images_are_named_for_a_reader_who_has_to_find_them() -> None:
    """A registry path nothing in the documentation prints is one somebody has to guess. The
    deployment page that tells them to pull is the page that has to say what."""
    integrated = (ROOT / "docs/deployment/integrated.md").read_text()

    assert "ghcr.io/" in integrated, "the integrated deployment page never names the registry"
    assert "RELEASE=" in integrated, "nothing tells a reader how to run a published version"


def test_the_compositions_are_accepted_by_compose_itself() -> None:
    """The one check that is about Compose's semantics rather than the files' content: a `!reset`
    spelled `!resets`, a merge key that silently lands as a mapping, an overlay whose anchor is
    defined below its use. All three parse as YAML and all three break the composition.

    **Allowed to skip**, and the only test here that is: it shells out to `docker compose`, which
    is present on a GitHub-hosted runner and on a developer's machine and is not guaranteed
    anywhere. A skip is honest about that rather than failing for the environment; what it must
    never do is pass by *not looking*, which is why the skip says so by name.
    """
    import shutil
    import subprocess

    if not shutil.which("docker"):
        pytest.skip("no docker binary; the composition is checked by running it (FRD-138 §7)")

    environment = {
        "AIRA_VERSION": "v0.0.0-test",
        "AIRA_POSTGRES_HOST": "db.example.internal",
        "AIRA_KAFKA_BOOTSTRAP_SERVERS": "broker.example.internal:9093",
        "AIRA_OIDC_ISSUER": "https://id.example.com/realms/aira",
        "AIRA_OIDC_JWKS_URI": "https://id.example.com/realms/aira/protocol/openid-connect/certs",
        "AIRA_REDIS_URL": "",
        "VAULT_ADDR": "",
    }
    import os

    for composition in (
        compose_files.RELEASE,
        compose_files.SHOWCASE_RELEASE,
        compose_files.INTEGRATED,
        compose_files.INTEGRATED_RELEASE,
    ):
        done = subprocess.run(
            ["docker", "compose", *compose_files.args(composition), "config"],
            capture_output=True,
            text=True,
            env={**os.environ, **environment},
            cwd=ROOT,
        )
        assert done.returncode == 0, (
            f"{[path.name for path in composition]} is not a valid composition:\n{done.stderr}"
        )


def test_every_required_variable_is_documented_where_a_deployment_looks() -> None:
    """A variable Compose refuses to start without is one a reader must be able to look up. The
    settings reference is generated from the settings classes, which these are not part of — they
    are Compose's own — so they are documented by hand or not at all."""
    configuration = (ROOT / "docs/CONFIGURATION.md").read_text()

    for variable in (VERSION_VARIABLE, "AIRA_IMAGE_REGISTRY", "AIRA_PULL_POLICY"):
        assert variable in configuration, (
            f"{variable} decides which code a deployment runs and is in no reference"
        )


def test_the_overlays_are_valid_yaml_without_the_compose_tags_being_silently_dropped() -> None:
    """A guard on the loader this whole file reads through. `yaml.safe_load` raises on `!reset`;
    a loader that mapped the tag to the *string* `"null"` instead of `None` would make
    `test_nothing_in_the_release_overlay_can_still_be_built` pass on a file that resets nothing.
    """
    with pytest.raises(yaml.YAMLError):
        yaml.safe_load(compose_files.RELEASE_ONLY.read_text())

    assert compose_files.load(compose_files.RELEASE_ONLY)["services"]["gateway"]["build"] is None
