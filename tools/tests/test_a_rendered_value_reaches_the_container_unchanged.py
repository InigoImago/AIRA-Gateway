"""A value `config_render` writes is the value the container receives, byte for byte.

The rendered `.env` is read by Docker Compose, not by Python. Compose interpolates `$` and
unescapes `\\b`, `\\n` and friends inside a double-quoted value, so a renderer that double-quoted
everything unusual handed the gateway a different value than the config file stated: a redaction
pattern's `\\b` arrived as a backspace and matched nothing — organisation-specific credentials then
stored unredacted, with no error anywhere — and `db$prod` arrived as `db`.

The round trip that should have caught it read the file back with a reader of its own that merely
stripped the quotes: a stand-in more permissive than the thing it stood in for, so it agreed with
the renderer by construction. Two checks replace it:

- **Compose itself** reads the rendered file (`docker compose config`, a scratch project, nothing
  started) and must report exactly the rendered values. Skipped only where Docker is absent.
- **Hermetically**, the renderer's own reader and the grammar it writes, so the mutation harness can
  break the quoting and see a test notice without a daemon.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import config_render
import pytest

#: Values Compose interprets one way or another, each the shape of something real: a redaction
#: pattern list, a password-like host, a role map, a URL with a fragment, a quote, a trailing
#: backslash, a line break, and all of them at once.
AWKWARD = {
    "PROBE_REGEX": r"\bACME-\d{8}\b;\bsk_[a-z]+\b",
    "PROBE_DOLLAR": "db$prod",
    "PROBE_BRACED": "${HOME}",
    "PROBE_ROLES": "global-admin=/aira/a;it-security=/aira/b",
    "PROBE_URL": "https://example.com/a?b=1#frag",
    "PROBE_SPACES": "  a value with spaces # and a hash  ",
    "PROBE_QUOTE": "it's",
    "PROBE_DOUBLE": 'say "hi"',
    "PROBE_TRAILING_BACKSLASH": "C:\\dir\\",
    "PROBE_LINES": "one\ntwo",
    "PROBE_EVERYTHING": 'it\'s $x and \\b and "q" and $$y\\',
    "PROBE_EMPTY": "",
    "PROBE_PLAIN": "eu,europe-west1",
}


def _rendered(tmp_path: Path) -> str:
    source = tmp_path / "installation.yaml"
    source.write_text("# a stand-in source, for the stamp\n")
    return config_render.as_env_file(AWKWARD, source)


def test_the_renderers_reader_reads_back_what_it_wrote(tmp_path: Path) -> None:
    assert config_render._env_pairs(_rendered(tmp_path)) == AWKWARD


def test_no_value_is_left_to_compose_s_double_quote_processing() -> None:
    """The defect's own shape, checked on the text: a value Compose would interpolate or unescape
    is single-quoted, and the few that must be double-quoted carry every escape spelled out."""
    for name, value in AWKWARD.items():
        written = config_render.env_value(value)
        if written.startswith('"'):
            inner = written[1:-1]
            assert "'" in value or "\n" in value or value.endswith("\\"), (
                f"{name} is double-quoted although single quotes would carry it literally"
            )
            # Every `$` doubled, every backslash the start of an escape Compose knows.
            assert "$" not in inner.replace("$$", ""), name
            assert not re.search(r"\\(?![\"nr])", inner.replace("\\\\", "")), name
        elif written.startswith("'"):
            assert "'" not in value and not value.endswith("\\"), name
        else:
            assert re.fullmatch(r"[A-Za-z0-9_./:@%+=-]+", written), name


def test_a_regular_expression_is_written_as_it_was_given() -> None:
    assert config_render.env_value(r"\bDE\d{20}\b") == r"'\bDE\d{20}\b'"


@pytest.mark.skipif(shutil.which("docker") is None, reason="no Docker on this machine")
def test_compose_hands_the_container_exactly_the_rendered_values(tmp_path: Path) -> None:
    """Compose's own reading, on a scratch project: `config` renders, it starts nothing."""
    env_file = tmp_path / ".env"
    env_file.write_text(_rendered(tmp_path))
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        json.dumps(
            {
                "services": {
                    "probe": {
                        "image": "busybox",
                        # `-` without `:` so an empty value stays empty rather than "UNSET".
                        "environment": {name: f"${{{name}-UNSET}}" for name in AWKWARD},
                    }
                }
            }
        )
    )
    # Without the probe names, so a variable in this process cannot stand in for the file.
    environment = {key: value for key, value in os.environ.items() if key not in AWKWARD}
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            f"aira-render-probe-{os.getpid()}",
            "--env-file",
            str(env_file),
            "-f",
            str(compose),
            "config",
            "--format",
            "json",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=120,
    )
    if result.returncode != 0 and "Cannot connect" in result.stderr:
        pytest.skip("Docker is installed and its daemon is not answering")
    assert result.returncode == 0, result.stderr

    received = json.loads(result.stdout)["services"]["probe"]["environment"]

    assert {
        name: config_render.from_compose_config(value) for name, value in received.items()
    } == AWKWARD
