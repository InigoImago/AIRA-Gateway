"""The AppRole secret-id never lands where git would commit it, and is never readable by others.

`tools/vault_setup.py` mints the one credential Vault cannot hold — the secret-id that, with the
role id it prints, reads every AIRA secret — and writes it to a file. Its default target was a
**tracked** file, so every run left a modified file in `git status` for the next `git commit -a`
to publish, and the integrated runbook runs the tool against a production Vault from a checkout.
The file was also written world-readable and chmod'ed afterwards.

Three properties, each checked against what git and the file system say rather than against the
code's own idea of them:

1. no tracked file holds a secret-id;
2. the tool refuses a target git would commit — tracked, or unignored inside a work tree — before
   it asks Vault for anything;
3. the file is `0600` from the first byte, whatever existed before and whatever the umask is.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
import vault_setup

ROOT = Path(__file__).resolve().parents[2]

#: A file name that says it holds an AppRole secret-id — `.vault-secret-id`, `vault_secret_id`.
#: Anchored at the end, so a source file merely *about* secret-ids, this one, is not taken for one.
SECRET_ID_NAME = re.compile(r"secret[-_]id$", re.IGNORECASE)


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=True,
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "the test suite",
            "GIT_AUTHOR_EMAIL": "tests@example.invalid",
            "GIT_COMMITTER_NAME": "the test suite",
            "GIT_COMMITTER_EMAIL": "tests@example.invalid",
        },
    ).stdout


@pytest.fixture
def scratch(tmp_path: Path) -> Path:
    """A repository of its own, with one tracked file and one ignored directory."""
    repository = tmp_path / "checkout"
    repository.mkdir()
    _git(repository, "init", "-q")
    (repository / ".gitignore").write_text("private/\n")
    (repository / "tracked-secret-id").write_text("committed\n")
    _git(repository, "add", ".gitignore", "tracked-secret-id")
    _git(repository, "commit", "-q", "-m", "scratch")
    return repository


def test_no_tracked_file_holds_a_secret_id() -> None:
    """**The leak itself.** A secret-id was committed in a tracked file, three times over.

    Checked on the working tree, so the test is green as soon as the file is gone from it and red
    again if anybody restores it; committing the removal is what takes it out of the next clone.
    """
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    holding = [
        name
        for name in tracked
        if SECRET_ID_NAME.search(Path(name).name)
        and (ROOT / name).is_file()
        and (ROOT / name).read_text(errors="replace").strip()
    ]

    assert not holding, (
        f"{holding} hold a secret-id under version control. Delete them, commit the deletion, and "
        "treat every secret-id they ever held as compromised: rotate it in Vault."
    )


def test_the_default_target_is_not_a_path_git_would_commit() -> None:
    assert vault_setup.committable(vault_setup.DEFAULT_SECRET_ID_FILE) is None, (
        f"{vault_setup.DEFAULT_SECRET_ID_FILE} is a path git would commit, so every run of "
        "`make vault-init` leaves a credential for the next commit to publish"
    )


def test_a_tracked_target_is_refused(scratch: Path) -> None:
    assert "tracks it" in str(vault_setup.committable(scratch / "tracked-secret-id"))


def test_an_unignored_target_inside_a_checkout_is_refused(scratch: Path) -> None:
    """`git add -A` takes an untracked file as readily as `commit -a` takes a tracked one."""
    assert "not ignored" in str(vault_setup.committable(scratch / "loose" / "secret-id"))


def test_an_ignored_target_or_one_outside_any_checkout_is_accepted(
    scratch: Path, tmp_path: Path
) -> None:
    assert vault_setup.committable(scratch / "private" / "secret-id") is None
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    assert vault_setup.committable(outside / "secret-id") is None


def test_a_refused_target_is_refused_before_vault_is_asked(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A secret-id minted and then refused would be one more valid credential for nothing."""
    asked: list[Any] = []
    monkeypatch.setattr(vault_setup, "setup", lambda *a, **k: asked.append(a) or {})

    code = vault_setup.main(["--secret-id-file", str(scratch / "tracked-secret-id")])

    assert code == 2
    assert asked == []
    assert (scratch / "tracked-secret-id").read_text() == "committed\n"
    assert "refusing" in capsys.readouterr().err


def test_the_file_is_private_from_its_first_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never observable with another mode: not while it is written, and not because an older file
    with a looser mode was there first. Under a permissive umask, which is where it would show."""
    target = tmp_path / "secrets" / "vault-secret-id"
    target.parent.mkdir()
    target.write_text("old")
    target.chmod(0o644)

    seen: list[int] = []
    real_replace = os.replace

    def watching(source: Any, destination: Any) -> None:
        seen.append(stat.S_IMODE(os.stat(source).st_mode))
        real_replace(source, destination)

    monkeypatch.setattr(vault_setup.os, "replace", watching)
    previous = os.umask(0)
    try:
        vault_setup.write_secret(target, "new-secret-id")
    finally:
        os.umask(previous)

    assert seen == [0o600], "the secret-id was in a file others could read before it was moved"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "new-secret-id"
    assert [p.name for p in target.parent.iterdir()] == ["vault-secret-id"], "a temporary is left"


def test_the_role_mints_secret_ids_that_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret-id with no lifetime is read access to every secret for good. The lifetime is set on
    the role, and no use count is, because each process start is one login."""
    sent: dict[str, Any] = {}

    def vault(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/v1/auth/approle/role/{vault_setup.ROLE_NAME}" and request.method == "POST":
            sent.update(__import__("json").loads(request.content))
        if path.endswith("/role-id"):
            return httpx.Response(200, json={"data": {"role_id": "role"}})
        if path.endswith("/secret-id"):
            return httpx.Response(
                200, json={"data": {"secret_id": "minted", "secret_id_ttl": 2592000}}
            )
        if path in ("/v1/sys/mounts", "/v1/sys/auth"):
            return httpx.Response(200, json={"data": {"secret/": {}, "approle/": {}}})
        return httpx.Response(204)

    monkeypatch.setattr(
        vault_setup,
        "_client",
        lambda address, token: httpx.Client(base_url=address, transport=httpx.MockTransport(vault)),
    )

    result = vault_setup.setup("http://vault.test", "token", "secret", "aira", {})

    assert sent["secret_id_ttl"] == vault_setup.DEFAULT_SECRET_ID_TTL
    assert "secret_id_num_uses" not in sent
    assert result["secret_id"] == "minted"
    assert result["secret_id_ttl"] == 2592000
