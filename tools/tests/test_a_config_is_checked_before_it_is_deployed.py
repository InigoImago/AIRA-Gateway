"""`make config-check` asks both planes whether they would start, before anything is deployed.

**The failure it exists for.** An `AIRA_ENVIRONMENT` that is not `local` turns on a list of
hardening checks per plane (`ADR-0015`), and for most deployments the first time those are all met
is when a container exits during a maintenance window with a message nobody was watching for. The
answers come from the product's own `unsafe_settings`, in a subprocess holding only the rendered
environment — a re-implementation would agree with itself and not with the service.

Three outcomes have to stay apart, and the tests below are mostly about that:

- **the file's own problems** — a wildcard `ALLOWED_HOSTS`, `PLAINTEXT` Kafka, no audience;
- **a credential the file deliberately does not carry** — `config_render.FORBIDDEN` is that list,
  and a refusal naming one of those is the configuration being *right* with Vault holding the
  other half. Counting it against the file would train a reader to ignore the output;
- **Vault declared and unusable** — not a pass and not the file's fault. Reporting it as either is
  the permissive stand-in this project keeps paying for.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_check  # noqa: E402
import config_render  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted((ROOT / "config").glob("*.example.yaml"))


def test_there_are_examples_to_check() -> None:
    assert EXAMPLES, "no config examples found — every test below would pass vacuously"


@pytest.mark.parametrize("example", EXAMPLES, ids=lambda p: p.name)
def test_every_shipped_example_is_accepted_or_says_exactly_why(example: Path) -> None:
    """The examples are what somebody copies, so none may carry a problem of its own.

    `--without-vault`, because the integrated example names a Vault that no test machine can
    reach — and the point of this suite is the file, not the network.
    """
    code, lines = config_check.check(example, without_vault=True)
    assert code == 0, "\n".join(lines)


def test_a_credential_the_file_cannot_carry_is_not_counted_against_it() -> None:
    """`AIRA_SECRET_KEY` and `AIRA_POSTGRES_PASSWORD` come from Vault by design (`FRD-116`).

    The integrated example is refused for exactly those two and nothing else, which is the
    strongest statement this check can make about it: complete, and missing only what a config
    file must never hold.
    """
    integrated = ROOT / "config" / "integrated.example.yaml"
    if not integrated.is_file():
        pytest.skip("no integrated example")
    code, lines = config_check.check(integrated, without_vault=True)
    body = "\n".join(lines)
    assert code == 0, body
    flagged = [line for line in lines if line.lstrip().startswith("·")]
    assert flagged, f"expected credentials to be named as Vault's half:\n{body}"
    assert all(any(name in line for name in config_render.FORBIDDEN) for line in flagged), body
    assert not [line for line in lines if line.lstrip().startswith("!")], body


def test_a_real_problem_is_reported_and_exits_non_zero(tmp_path: Path) -> None:
    """Red before green: a file that a plane would refuse must be refused here."""
    source = ROOT / "config" / "integrated.example.yaml"
    broken = tmp_path / "broken.yaml"
    broken.write_text(
        source.read_text(encoding="utf-8").replace(
            "  security_protocol: SASL_SSL", "  security_protocol: PLAINTEXT"
        ),
        encoding="utf-8",
    )
    code, lines = config_check.check(broken, without_vault=True)
    body = "\n".join(lines)
    assert code == 1, body
    assert "PLAINTEXT" in body
    assert [line for line in lines if line.lstrip().startswith("!")], body


def test_a_file_that_does_not_render_is_its_own_answer(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("core:\n  secret_key: a-credential-in-the-file\n", encoding="utf-8")
    code, lines = config_check.check(bad)
    assert code == 2 and "credential" in "\n".join(lines)


def test_vault_declared_and_unusable_is_neither_a_pass_nor_the_files_fault() -> None:
    """The third outcome, and the one a laxer version would fold into one of the other two."""
    integrated = ROOT / "config" / "integrated.example.yaml"
    if not integrated.is_file():
        pytest.skip("no integrated example")
    code, lines = config_check.check(integrated)
    body = "\n".join(lines)
    assert code == config_check.VAULT_UNAVAILABLE, body
    assert "cannot use it" in body and "--without-vault" in body
    assert "thing(s) this file has to answer for" not in body


def test_the_environment_the_planes_are_asked_in_is_only_the_rendered_one(monkeypatch) -> None:
    """A check that inherits a value the deployment will not have is worth nothing.

    Set a variable that would *fix* a problem and confirm the answer does not change: the
    subprocess must never see this process's environment.
    """
    source = ROOT / "config" / "integrated.example.yaml"
    if not source.is_file():
        pytest.skip("no integrated example")
    before, _ = config_check.check(source, without_vault=True)
    monkeypatch.setenv("AIRA_SECRET_KEY", "a-value-this-process-happens-to-have")
    monkeypatch.setenv("AIRA_POSTGRES_PASSWORD", "and-another")
    after, lines = config_check.check(source, without_vault=True)
    assert after == before, "\n".join(lines)
    assert any("AIRA_SECRET_KEY" in line for line in lines), "\n".join(lines)


def test_both_planes_are_asked() -> None:
    """One plane answering for both is how half a configuration passes a whole check."""
    assert set(config_check.PLANES) == {"gateway", "management"}
    for spec in config_check.PLANES.values():
        assert Path(spec["paths"][0]).is_dir(), spec
        assert spec["checker_module"].endswith("security")
