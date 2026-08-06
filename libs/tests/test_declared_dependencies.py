"""Every third-party import in `aira_common` is a declared dependency.

This exists because the project has now learned the same lesson twice, in the same way.

`aira_common.oidc` imported `pyjwt` without declaring it. It worked everywhere anybody looked —
the gateway declares `pyjwt`, and a `uv sync` of the workspace installs every package's
dependencies into one environment — and then the **management image**, which contains
`aira_common` but not the gateway, failed to import at startup. The fix was a line in
`pyproject.toml` and a comment explaining it.

Then `aira_common.secrets` imported `httpx` (`FRD-116`) and did exactly the same thing: 1028
hermetic tests green, a live Vault read working, and the management migration container dead on
`ModuleNotFoundError`.

The pattern is worth naming, because it will keep happening otherwise. **A shared library's
dependencies cannot be validated by any environment that also installs its consumers**, and this
repository's dev environment, its test runner and its coverage gate are all such an environment.
The only honest check is a static one, which is this file — it costs milliseconds and replaces a
failure that costs a deploy.
"""

from __future__ import annotations

import ast
import sys
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "aira_common"
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _declared() -> set[str]:
    """The distribution names `aira_common` says it needs, normalised to import names.

    Crude on purpose: `pyjwt[crypto]>=2.9` becomes `pyjwt`, and the exceptions where a
    distribution and its import differ are listed below rather than resolved from metadata. A
    clever version of this would need the packages installed, which is the very assumption the
    test exists to avoid depending on.
    """
    data = tomllib.loads(PYPROJECT.read_text())
    names: set[str] = set()
    for requirement in data["project"]["dependencies"]:
        name = requirement.split(";")[0].split("[")[0]
        for separator in (">=", "==", "<=", "~=", ">", "<", "!="):
            name = name.split(separator)[0]
        names.add(name.strip().lower().replace("-", "_"))
    return names


#: Distributions whose import name is not their package name. Listed, because deriving it
#: requires importing them — and this test must work in an environment where they are missing.
IMPORT_NAMES = {
    "pyjwt": "jwt",
    "opentelemetry_sdk": "opentelemetry",
    "opentelemetry_exporter_otlp_proto_http": "opentelemetry",
    "pydantic_settings": "pydantic_settings",
}


def _importable() -> set[str]:
    declared = _declared()
    allowed = {IMPORT_NAMES.get(name, name) for name in declared}
    # `pydantic-settings` brings `pydantic`, and `aiokafka` is imported as itself. Anything else a
    # dependency happens to pull in is **not** allowed: relying on a transitive dependency is the
    # same mistake one level down, and it breaks on the day that package drops it.
    return allowed | {"pydantic"}


def _third_party_imports(path: Path) -> set[str]:
    """Top-level modules imported by one file, excluding the standard library and ourselves."""
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module.split(".")[0])
    return {
        module
        for module in modules
        if module not in sys.stdlib_module_names and module != "aira_common"
    }


def _modules() -> list[Path]:
    return sorted(PACKAGE.rglob("*.py"))


def test_the_package_has_modules_to_check() -> None:
    """A guard on the guard: if the glob ever stops matching, every test below passes vacuously
    and the check quietly stops existing."""
    assert len(_modules()) > 5


@pytest.mark.parametrize("module", _modules(), ids=lambda path: path.name)
def test_every_import_is_declared(module: Path) -> None:
    """The check that would have caught both incidents before a container did."""
    allowed = _importable()
    undeclared = sorted(_third_party_imports(module) - allowed)

    assert not undeclared, (
        f"{module.name} imports {undeclared}, which libs/pyproject.toml does not declare. "
        "It resolves here because the workspace installs every package's dependencies into one "
        "environment — and it will fail on import in any image that contains aira_common without "
        "the consumer that happens to declare it."
    )


def test_httpx_is_declared_because_the_secrets_loader_needs_it() -> None:
    """Named explicitly, so the reason survives somebody tidying the dependency list."""
    assert "httpx" in _declared()


def test_pyjwt_is_declared_because_the_token_verifier_needs_it() -> None:
    assert "pyjwt" in _declared()
