"""The showcase's data-mode switch edits one line and shows nothing else (`FRD-627`).

`deploy/compose/.env` holds the installation's real credentials, so the switch is held to two
things besides working: every other line survives untouched, and nothing it prints carries a
value from the file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import showcase_mode

SECRET_LINE = "AIRA_GOOGLE_API_KEY=very-secret-value"


@pytest.fixture(autouse=True)
def _the_real_file_is_never_touched() -> Iterator[None]:
    """The real `deploy/compose/.env` holds credentials; a test that wrote it once is why this
    exists. Compared whole, before and after every test."""
    real = showcase_mode.ROOT / "deploy" / "compose" / ".env"
    before = real.read_bytes() if real.exists() else None
    yield
    assert (real.read_bytes() if real.exists() else None) == before, "a test wrote the real .env"


@pytest.fixture(autouse=True)
def env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f"# a comment\n{SECRET_LINE}\n#AIRA_STRICT_DATA_MODE=true\nAIRA_CURRENCY=EUR\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(showcase_mode, "ENV_FILE", path)
    return path


def test_off_appends_one_active_line_and_keeps_every_other(env_file: Path) -> None:
    before = env_file.read_text(encoding="utf-8").splitlines()

    showcase_mode.set_mode(False, env_file)

    after = env_file.read_text(encoding="utf-8").splitlines()
    assert after[: len(before)] == before, "the example line and the secrets stay as they were"
    assert after[len(before) :] == ["AIRA_STRICT_DATA_MODE=false"]
    assert showcase_mode.configured(env_file) is False


def test_on_replaces_the_active_line_rather_than_adding_another(env_file: Path) -> None:
    showcase_mode.set_mode(False, env_file)
    showcase_mode.set_mode(True, env_file)

    active = [
        line
        for line in env_file.read_text(encoding="utf-8").splitlines()
        if line.startswith("AIRA_STRICT_DATA_MODE=")
    ]
    assert active == ["AIRA_STRICT_DATA_MODE=true"]
    assert showcase_mode.configured(env_file) is True


def test_absent_and_commented_out_mean_the_default(tmp_path: Path, env_file: Path) -> None:
    assert showcase_mode.configured(env_file) is True
    assert showcase_mode.configured(tmp_path / "missing.env") is True


def test_a_rendered_env_is_refused_untouched(env_file: Path) -> None:
    rendered = "# aira-config-source: config/mine.yaml\nAIRA_CURRENCY=EUR\n"
    env_file.write_text(rendered, encoding="utf-8")

    with pytest.raises(showcase_mode.Refused, match=r"core\.strict_data_mode"):
        showcase_mode.set_mode(False, env_file)
    assert env_file.read_text(encoding="utf-8") == rendered


@pytest.mark.parametrize("argument", ["on", "off", "status"])
def test_nothing_it_prints_carries_a_value_from_the_file(
    argument: str, env_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(showcase_mode, "running", lambda **_: None)

    assert showcase_mode.main([argument]) == 0

    printed = capsys.readouterr()
    assert "very-secret-value" not in printed.out + printed.err
    assert "strict data mode" in printed.out


def test_status_says_when_the_running_stack_is_in_the_other_mode(
    env_file: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    showcase_mode.set_mode(False, env_file)
    monkeypatch.setattr(showcase_mode, "running", lambda **_: True)

    showcase_mode.main(["status"])

    assert "They differ" in capsys.readouterr().out


def test_anything_else_is_refused_by_name(capsys: pytest.CaptureFixture) -> None:
    assert showcase_mode.main(["maybe"]) == 2
    assert "on|off|status" in capsys.readouterr().err


def test_the_pseudonym_key_is_drawn_once_and_never_shown(
    env_file: Path, capsys: pytest.CaptureFixture
) -> None:
    assert showcase_mode.main(["key"]) == 0
    first = env_file.read_text(encoding="utf-8")
    assert showcase_mode.main(["key"]) == 0

    assert env_file.read_text(encoding="utf-8") == first, "drawn once, then kept"
    (line,) = [entry for entry in first.splitlines() if entry.startswith("AIRA_PSEUDONYM_SECRET=")]
    value = line.partition("=")[2]
    assert len(value) == 64
    assert value not in capsys.readouterr().out
    assert SECRET_LINE in first


def test_a_rendered_env_gets_no_drawn_key(env_file: Path) -> None:
    rendered = "# aira-config-source: config/mine.yaml\nAIRA_CURRENCY=EUR\n"
    env_file.write_text(rendered, encoding="utf-8")

    assert showcase_mode.ensure_pseudonym_secret(env_file) is False
    assert env_file.read_text(encoding="utf-8") == rendered
