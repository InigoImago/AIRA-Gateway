"""Which Compose file is which — decided once, because a split is only safe if nobody guesses.

**The stack is described by three files and they are not interchangeable.** On 2026-08-26
`docker-compose.apps.yml` was 625 lines, and roughly a third of it — a development Keycloak realm,
a `-dev` Vault refilled on every start, five seeded demo accounts — was there for the showcase.
Anybody deploying AIRA onto their own Keycloak, Vault and Postgres had to work out which half
applied to them. So the showcase moved into `docker-compose.showcase.yml`.

A split like that is cheap to make and expensive to get wrong, because **sixteen places named the
files by hand**: the Makefile, `tools/config_render.py`, `tools/stack_addresses.py` and thirteen
tests. Each one would have had to be found. The ones that were missed would not fail loudly —
they would quietly check two files where three now exist, and report that a variable reaches no
container when it reaches one in the file they did not read.

Same rule as `tools/stack_addresses.py`, one layer up: **one place decides, everybody asks.**
`tools/tests/test_one_owner_for_the_stack_addresses.py` already fails on a stray literal address;
`tools/tests/test_the_core_stack_carries_no_demo.py` fails on a demo service that reappears in the
core file.

Which list to ask for:

- `CORE` — infrastructure plus the application processes. **This is the product**: what a real
  deployment runs, and what a reader should be able to read end to end without skipping demo
  scaffolding. Usable on its own; `make up-apps` runs exactly this.
- `SHOWCASE` — `CORE` plus the demo provisioning. `make up-full` and `make showcase` run this.
- `ALL` — the same three files. Named apart from `SHOWCASE` because the *reason* differs: a check
  that asks "does any container receive this variable" must read every file that defines one, and
  that question is not about the demo.
- `RELEASE`, `SHOWCASE_RELEASE`, `INTEGRATED`, `INTEGRATED_RELEASE` — the compositions that take
  the three application images **from the registry** instead of building them, and the one that
  runs them against infrastructure somebody else provides (`FRD-138`). Overlays, not files of
  their own: each entry in them re-points a service `APPS` already defines.

Read a file with `load()` rather than `yaml.safe_load`, which raises on the two tags an overlay
needs — `!reset` and `!override`.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_DIR = ROOT / "deploy" / "compose"

INFRA = COMPOSE_DIR / "docker-compose.yml"
APPS = COMPOSE_DIR / "docker-compose.apps.yml"
SHOWCASE_ONLY = COMPOSE_DIR / "docker-compose.showcase.yml"
RELEASE_ONLY = COMPOSE_DIR / "docker-compose.release.yml"
EXTERNAL_ONLY = COMPOSE_DIR / "docker-compose.external.yml"

#: What a real deployment runs.
CORE: tuple[Path, ...] = (INFRA, APPS)

#: The demo, which is `CORE` with provisioning layered on top.
SHOWCASE: tuple[Path, ...] = (INFRA, APPS, SHOWCASE_ONLY)

#: Every file that **defines** a service, for checks that must not miss one.
#:
#: The two overlays below are deliberately absent, and the distinction is the reason this is not
#: simply "every compose file". They define no service: every entry in them names one of
#: `APPS`' and changes one property of it. A check asking *"does any container receive this
#: variable"* or *"does every image say how it is built"* is asking about the files above, and
#: adding these two would answer it twice over a service that exists once.
ALL: tuple[Path, ...] = SHOWCASE

#: `CORE` with the three application images taken from the registry instead of built here
#: (`FRD-138`). Needs `AIRA_VERSION`; Compose refuses the composition without it, on purpose.
RELEASE: tuple[Path, ...] = (INFRA, APPS, RELEASE_ONLY)

#: The demo, on published images — what `make showcase RELEASE=vX.Y.Z` runs. The order matters:
#: `RELEASE_ONLY` must come after `SHOWCASE_ONLY` so that it also re-points the seed, which
#: `extends:` out of `APPS` and would otherwise be the one container built from source.
SHOWCASE_RELEASE: tuple[Path, ...] = (INFRA, APPS, SHOWCASE_ONLY, RELEASE_ONLY)

#: The applications alone, against infrastructure the operator provides. **No `INFRA`** — that is
#: the whole point — so every address becomes required and every dependency on a container this
#: composition does not start is removed.
INTEGRATED: tuple[Path, ...] = (APPS, EXTERNAL_ONLY)

#: The same, running published images rather than a build of this checkout: the shape
#: `docs/deployment/integrated.md` §5a describes.
INTEGRATED_RELEASE: tuple[Path, ...] = (APPS, EXTERNAL_ONLY, RELEASE_ONLY)

#: **There is no fourth file.** There was one — `docker-compose.lab.yml`, an overlay for trying a
#: second telemetry destination alongside the stack, kept out of every list above so that an
#: experiment could be undone by leaving one `-f` out.
#:
#: It was removed on 2026-09-02, and the reason is worth keeping: its whole existence rested on
#: *"the Collector merges nothing, a `--config` is the whole configuration"*, so a second
#: destination meant a **copy** of `collector-config.yaml` — eighty duplicated lines drifting
#: apart. Measured again against collector-contrib 0.157, that is no longer true: repeated
#: `--config` flags merge, deeply enough to keep each pipeline's receivers and processors. So the
#: second destination became a fragment merged on top (`otel/collector-forward.yaml`), selected by
#: `AIRA_OTEL_FORWARD_CONFIG`, and the reference stack gained it without gaining a file.
#:
#: The rule the removed guard encoded still holds for whatever comes next: **a compose file nobody
#: registers here is one that thirteen tests quietly do not read.**

#: Services that exist only for the demo. The core file must contain none of them — a rule with a
#: test, because the cheapest way to undo this split is to add "just one" demo service back where
#: the anchors happen to be.
DEMO_ONLY = ("keycloak-init", "vault-init", "management-seed")


def args(files: tuple[Path, ...]) -> list[str]:
    """`-f a -f b …`, in the order Compose must read them."""
    return [arg for path in files for arg in ("-f", str(path))]


def load(path: Path) -> dict:
    """One Compose document, parsed — including the two tags an overlay cannot do without.

    `!reset` removes an inherited key and `!override` replaces one instead of merging into it.
    Both are Compose's own, and `yaml.safe_load` raises `ConstructorError` on either: *"could not
    determine a constructor for the tag '!reset'"*. A test that reads an overlay with `safe_load`
    therefore does not fail about the overlay — it fails about YAML, from inside a helper, and the
    first guess is that the file is malformed.

    Both are mapped to the node's own value, so `build: !reset null` reads as `build: None`. That
    is what a test wants to assert: the key is *present and emptied*, which is a different
    statement from absent, and the difference is the whole mechanism.
    """

    # **Imported here, not at the top.** `tools/stack_addresses.py` imports this module and the
    # Makefile runs *that* with the system `python3`, before any virtual environment exists —
    # `STACK_ADDRESSES := $(shell python3 tools/stack_addresses.py make)`, evaluated by every
    # target. A module-level `import yaml` therefore broke `make down` with a traceback about PyYAML
    # from a file about compose paths. The one function that parses is the one that needs the
    # parser.
    import yaml

    def untagged(loader: yaml.SafeLoader, node: yaml.Node) -> object:
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node, deep=True)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node, deep=True)
        # `!reset null` — the scalar is the word, so it is resolved the way an untagged one would
        # be rather than kept as the string "null".
        scalar = loader.construct_scalar(node)
        return yaml.safe_load(scalar) if scalar else None

    class _ComposeTags(yaml.SafeLoader):
        pass

    for tag in ("!reset", "!override"):
        _ComposeTags.add_constructor(tag, untagged)

    return yaml.load(path.read_text(), Loader=_ComposeTags) or {}
