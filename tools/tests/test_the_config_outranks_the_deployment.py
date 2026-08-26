"""The config file ranks above the deployment — and that is a claim only a check can make true.

`config/README.md` states a precedence: Vault, then the config file, then a Compose default, then
the settings class. The middle two are the fragile pair. Compose fills every gap it is given from
`${VAR:-default}`, so a value left empty, a variable the file does not name, a `.env` edited after
rendering, or a source edited without re-rendering all end the same way — the deployment runs on
something nobody chose, **and nothing says so**. That silence is the whole problem: an integrator
who mistypes a hostname sees a stack that starts.

So each test below is one way the deployment could quietly disagree with the file, and each asserts
the disagreement is *reported*. The tests that matter most are the ones where the stack looks fine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_render  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SHOWCASE = REPO / "config" / "showcase.example.yaml"
COMPOSE = [
    REPO / "deploy" / "compose" / "docker-compose.yml",
    REPO / "deploy" / "compose" / "docker-compose.apps.yml",
]


@pytest.fixture
def rendered(tmp_path: Path) -> tuple[Path, Path]:
    """A source and a `.env` rendered from it, both writable — the honest starting point."""
    source = tmp_path / "showcase.example.yaml"
    source.write_text(SHOWCASE.read_text(encoding="utf-8"), encoding="utf-8")
    env = tmp_path / ".env"
    env.write_text(
        config_render.as_env_file(config_render.render(config_render.load(source)), source),
        encoding="utf-8",
    )
    return source, env


def _no_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the file-against-source half. CI has no daemon; the Compose half is faked below."""
    monkeypatch.setattr(config_render, "_effective_environment", lambda _files: None)


def _containers(monkeypatch: pytest.MonkeyPatch, environment: dict[str, str]) -> None:
    """Stand in for `docker compose config`, in the shape the real one returns."""
    monkeypatch.setattr(
        config_render,
        "_effective_environment",
        lambda _files: {name: {value} for name, value in environment.items()},
    )


def test_a_freshly_rendered_file_is_clean(rendered, monkeypatch):
    source, env = rendered
    _no_docker(monkeypatch)
    problems = config_render.verify(env, COMPOSE)
    assert problems == [p for p in problems if p.startswith("note:")], problems


def test_a_hand_made_env_with_no_config_file_is_not_a_finding(tmp_path, monkeypatch):
    """The demo path ships one on purpose, and a check that cries wolf there is one nobody reads.

    This is the half that is easy to get wrong in the *safe*-looking direction: refusing every
    unstamped file sounds strict, and produces a warning on every `make up` of the demo — which is
    how the warning stops being read before the day it is finally right.
    """
    _no_docker(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("AIRA_CURRENCY=EUR\nAIRA_ENFORCE_BUDGETS=true\n", encoding="utf-8")
    problems = config_render.verify(env, COMPOSE)
    assert problems and all(p.startswith("note:") for p in problems), problems
    assert config_render.main(["--verify", str(env)]) == 0


def test_replacing_a_rendered_env_with_a_hand_written_one_is_refused(tmp_path, monkeypatch):
    """The takeover the marker exists for: the stamp leaves with the file that carried it.

    Render, then drop a hand-written `.env` over it. What remains looks exactly like the demo
    path — same absent stamp — and only the marker beside it knows the difference.
    """
    _no_docker(monkeypatch)
    source = tmp_path / "showcase.example.yaml"
    source.write_text(SHOWCASE.read_text(encoding="utf-8"), encoding="utf-8")
    env = tmp_path / ".env"
    assert config_render.main([str(source), "-o", str(env)]) == 0
    assert config_render.marker_of(env).read_text(encoding="utf-8").strip() == str(source)
    env.write_text("AIRA_CURRENCY=CHF\n", encoding="utf-8")
    (problem,) = config_render.verify(env, COMPOSE)
    assert not problem.startswith("note:")
    assert str(source) in problem and "no longer decides" in problem


def test_the_marker_holds_a_path_and_nothing_else(tmp_path):
    """It sits in a deployment directory, so it must never become a second place secrets land."""
    source = tmp_path / "showcase.example.yaml"
    source.write_text(SHOWCASE.read_text(encoding="utf-8"), encoding="utf-8")
    env = tmp_path / ".env"
    config_render.main([str(source), "-o", str(env)])
    body = config_render.marker_of(env).read_text(encoding="utf-8")
    assert body.strip() == str(source) and "=" not in body and len(body.splitlines()) == 1


def test_a_missing_env_file_is_named(tmp_path, monkeypatch):
    _no_docker(monkeypatch)
    (problem,) = config_render.verify(tmp_path / "nothing.env", COMPOSE)
    assert "does not exist" in problem


def test_a_source_that_vanished_is_named(rendered, monkeypatch):
    source, env = rendered
    _no_docker(monkeypatch)
    source.unlink()
    (problem,) = config_render.verify(env, COMPOSE)
    assert str(source) in problem and "gone" in problem


def test_editing_the_rendered_file_by_hand_is_caught(rendered, monkeypatch):
    """`.env` is the file an integrator will reach for, because it is the one they can read."""
    _source, env = rendered
    _no_docker(monkeypatch)
    env.write_text(
        env.read_text(encoding="utf-8").replace("AIRA_CURRENCY=EUR", "AIRA_CURRENCY=CHF"),
        encoding="utf-8",
    )
    assert any("AIRA_CURRENCY" in p and "edited" in p for p in config_render.verify(env, COMPOSE))


def test_deleting_a_line_from_the_rendered_file_is_caught(rendered, monkeypatch):
    _source, env = rendered
    _no_docker(monkeypatch)
    kept = [
        ln
        for ln in env.read_text(encoding="utf-8").splitlines()
        if not ln.startswith("AIRA_CURRENCY=")
    ]
    env.write_text("\n".join(kept) + "\n", encoding="utf-8")
    assert any("AIRA_CURRENCY" in p and "missing" in p for p in config_render.verify(env, COMPOSE))


def test_changing_the_source_without_re_rendering_is_caught(rendered, monkeypatch):
    """The stale render: both files are internally consistent, and one of them is a lie."""
    source, env = rendered
    _no_docker(monkeypatch)
    source.write_text(
        source.read_text(encoding="utf-8") + "\n# a comment is enough\n", encoding="utf-8"
    )
    assert any("has changed since" in p for p in config_render.verify(env, COMPOSE))


def test_a_variable_no_service_receives_is_reported(rendered, monkeypatch):
    """The knob that turns nothing: present in the file, absent from every container."""
    _source, env = rendered
    values = config_render.render(config_render.load(SHOWCASE))
    _containers(monkeypatch, {n: v for n, v in values.items() if n != "AIRA_CURRENCY"})
    assert any(
        "AIRA_CURRENCY" in p and "no service receives it" in p
        for p in config_render.verify(env, COMPOSE)
    )


def test_a_compose_default_taking_over_is_reported(rendered, monkeypatch):
    """The silent case this whole module exists for, and the one a running stack hides."""
    _source, env = rendered
    values = config_render.render(config_render.load(SHOWCASE))
    _containers(
        monkeypatch, {**values, "AIRA_ENFORCE_BUDGETS": "true"} | {"AIRA_ENFORCE_BUDGETS": "false"}
    )
    assert any(
        "AIRA_ENFORCE_BUDGETS" in p and "compose default is taking over" in p
        for p in config_render.verify(env, COMPOSE)
    )


def test_the_two_variables_the_deployment_decides_are_exempt_and_argued(rendered, monkeypatch):
    """An exemption is a decision, so each carries its reason and neither is a bare name."""
    _source, env = rendered
    values = config_render.render(config_render.load(SHOWCASE))
    _containers(
        monkeypatch, {n: v for n, v in values.items() if n not in config_render.COMPOSE_DECIDES}
    )
    assert not [
        p
        for p in config_render.verify(env, COMPOSE)
        if any(n in p for n in config_render.COMPOSE_DECIDES)
    ]
    for name, reason in config_render.COMPOSE_DECIDES.items():
        assert name.startswith("AIRA_") and len(reason.split()) >= 5, name


def test_docker_being_absent_is_said_rather_than_assumed(rendered, monkeypatch):
    """Half a check that reports itself as whole is worse than no check."""
    _source, env = rendered
    _no_docker(monkeypatch)
    assert any(
        p.startswith("note:") and "not checked" in p for p in config_render.verify(env, COMPOSE)
    )


def test_verify_exits_non_zero_when_something_disagrees(rendered, monkeypatch, capsys):
    """`make up` runs this; an exit code nobody sets is a check nobody can wire into anything."""
    _source, env = rendered
    _no_docker(monkeypatch)
    assert config_render.main(["--verify", str(env)]) == 0
    env.write_text(
        env.read_text(encoding="utf-8").replace("AIRA_CURRENCY=EUR", "AIRA_CURRENCY=CHF"),
        encoding="utf-8",
    )
    assert config_render.main(["--verify", str(env)]) != 0
    # stderr, deliberately: a difference is a diagnostic, and `make up` should show it in red.
    assert "AIRA_CURRENCY" in capsys.readouterr().err


def test_the_real_examples_all_render_a_file_that_verifies(tmp_path, monkeypatch):
    """Not the fixture's copy — the files that ship, each rendered and checked against itself."""
    _no_docker(monkeypatch)
    for source in sorted((REPO / "config").glob("*.example.yaml")):
        env = tmp_path / f"{source.stem}.env"
        env.write_text(
            config_render.as_env_file(config_render.render(config_render.load(source)), source),
            encoding="utf-8",
        )
        assert [p for p in config_render.verify(env, COMPOSE) if not p.startswith("note:")] == [], (
            source.name
        )
