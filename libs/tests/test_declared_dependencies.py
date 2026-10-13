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
import re
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
    "pydantic_settings": "pydantic_settings",
    # `protobuf` installs as the `google` namespace package — a distribution name that shares
    # nothing with what you type after `import`.
    "protobuf": "google",
}

#: Distributions that share one namespace, by the module prefix each installs; the longest prefix
#: that matches an import decides which one it needs. `opentelemetry` is five distributions here,
#: and reading it as one let a declared SDK stand in for an undeclared API, wire format or encoder.
NAMESPACE_DISTRIBUTIONS = {
    "opentelemetry": "opentelemetry_api",
    "opentelemetry.sdk": "opentelemetry_sdk",
    "opentelemetry.proto": "opentelemetry_proto",
    "opentelemetry.exporter.otlp.proto.http": "opentelemetry_exporter_otlp_proto_http",
    "opentelemetry.exporter.otlp.proto.common": "opentelemetry_exporter_otlp_proto_common",
    "opentelemetry.util.http": "opentelemetry_util_http",
    "opentelemetry.instrumentation": "opentelemetry_instrumentation",
}

#: A dotted module path, as a string an import is computed from.
_DOTTED = re.compile(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+")


def _importable() -> set[str]:
    """What a module may import: the declared distributions, as each is imported or required."""
    declared = _declared()
    namespaced = set(NAMESPACE_DISTRIBUTIONS.values())
    allowed = {name if name in namespaced else IMPORT_NAMES.get(name, name) for name in declared}
    # `pydantic-settings` brings `pydantic`, and `aiokafka` is imported as itself. Anything else a
    # dependency happens to pull in is **not** allowed: relying on a transitive dependency is the
    # same mistake one level down, and it breaks on the day that package drops it.
    return allowed | {"pydantic"}


def _required(module: str) -> str:
    """What importing ``module`` needs declared: its distribution inside a shared namespace, else
    its top-level name."""
    for prefix in sorted(NAMESPACE_DISTRIBUTIONS, key=len, reverse=True):
        if module == prefix or module.startswith(f"{prefix}."):
            return NAMESPACE_DISTRIBUTIONS[prefix]
    return module.split(".")[0]


def _is_import_module(node: ast.Call) -> bool:
    function = node.func
    name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
    return name in {"import_module", "__import__"}


def _third_party_imports(path: Path) -> set[str]:
    """The modules one file imports, in full, excluding the standard library and ourselves.

    Statements, and **strings**: a module imported by `importlib.import_module` needs its
    distribution as much as one imported by a statement. A literal argument names it; where the
    name is computed, every dotted name the file spells is taken as one it may import.
    """
    tree = ast.parse(path.read_text())
    modules: set[str] = set()
    computed = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Call) and _is_import_module(node) and node.args:
            argument = node.args[0]
            if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                modules.add(argument.value)
            else:
                computed = True
    if computed:
        modules.update(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _DOTTED.fullmatch(node.value)
        )
    return {
        module
        for module in modules
        if module.split(".")[0] not in sys.stdlib_module_names
        and module.split(".")[0] != "aira_common"
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
    undeclared = sorted({_required(name) for name in _third_party_imports(module)} - allowed)

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


def test_a_module_imported_by_its_name_is_counted() -> None:
    """`observability.payload` imports the OTLP encoders with `importlib.import_module`, and a check
    that read only statements saw none of them."""
    payload = PACKAGE / "observability" / "payload.py"
    assert "opentelemetry.exporter.otlp.proto.common.trace_encoder" in _third_party_imports(payload)


def test_one_distribution_in_a_namespace_does_not_admit_its_neighbours() -> None:
    assert _required("opentelemetry.trace") == "opentelemetry_api"
    assert _required("opentelemetry.sdk.trace") == "opentelemetry_sdk"
    assert _required("opentelemetry.proto.collector.trace.v1") == "opentelemetry_proto"
    assert _required("opentelemetry.exporter.otlp.proto.common.trace_encoder") == (
        "opentelemetry_exporter_otlp_proto_common"
    )
    assert _required("jwt.exceptions") == "jwt"
