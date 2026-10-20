"""The OpenCode budget plugin as the archive a release attaches.

A rollout — Terraform, a login script — downloads it from the GitHub Release and unpacks it into
OpenCode's configuration directory. What must hold is what that rollout relies on without looking:
unpacking puts the plugin, byte for byte, where OpenCode finds it, and nothing else; the developer's
own files are not overwritten; and the published checksum is the archive's, the same on every build.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

import opencode_plugin_archive as archive
import pytest

ROOT = Path(__file__).resolve().parents[2]


def _members(data: bytes) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as packed:
        return packed.getmembers()


def test_unpacking_it_puts_the_plugin_where_opencode_looks(tmp_path: Path) -> None:
    """The one member, at the path OpenCode 2 discovers and OpenCode 1's `tui.json` names, with the
    plugin's exact bytes — unpacked by `tar`, as a rollout does."""
    built, _ = archive.build("v1.2.3", tmp_path / "out")
    config = tmp_path / "opencode"
    config.mkdir()

    subprocess.run(["tar", "-xzf", str(built), "-C", str(config)], check=True)  # noqa: S603, S607

    unpacked = sorted(p.relative_to(config).as_posix() for p in config.rglob("*") if p.is_file())
    assert unpacked == ["plugins/aira-budget/tui.tsx"]
    assert (config / "plugins/aira-budget/tui.tsx").read_bytes() == archive.SOURCE.read_bytes()


def test_it_carries_no_file_of_the_developers_own(tmp_path: Path) -> None:
    """`tui.json` and `opencode.json` hold a developer's themes, keys and other plugins; an archive
    that carried one would erase them on every rollout. Only the plugin's directory is touched."""
    built, _ = archive.build("v1.2.3", tmp_path)

    names = [member.name for member in _members(built.read_bytes())]

    assert all(name.startswith("plugins/aira-budget/") for name in names), names


def test_the_file_is_an_ordinary_readable_file_owned_by_nobody(tmp_path: Path) -> None:
    """Unpacked as root by a provisioning tool, a member's recorded owner and mode are applied. A
    build machine's uid, or an executable bit, would land on every workstation."""
    built, _ = archive.build("v1.2.3", tmp_path)

    (member,) = _members(built.read_bytes())

    assert member.isfile()
    assert (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", "")
    assert member.mode == 0o644


def test_two_builds_of_one_version_are_the_same_bytes(tmp_path: Path) -> None:
    """The published checksum can be recomputed from a checkout, and a rollout comparing checksums
    does not reinstall an unchanged plugin. gzip writes a time and a file name into its header
    unless told not to."""
    first, _ = archive.build("v1.2.3", tmp_path / "a")
    second, _ = archive.build("v1.2.3", tmp_path / "b")

    assert first.read_bytes() == second.read_bytes()
    # Two builds in one second agree anyway, so the header is read: no time, no name (RFC 1952).
    header = first.read_bytes()[:10]
    assert header[4:8] == b"\0\0\0\0", "gzip recorded the build time"
    assert not header[3] & 0x08, "gzip recorded a file name"


def test_the_checksum_file_is_the_archives_and_sha256sum_accepts_it(tmp_path: Path) -> None:
    built, checksum = archive.build("v1.2.3", tmp_path)

    assert checksum.name == "opencode-aira-budget-v1.2.3.tar.gz.sha256"
    digest = hashlib.sha256(built.read_bytes()).hexdigest()
    assert checksum.read_text() == f"{digest}  {built.name}\n"
    subprocess.run(  # noqa: S603
        ["sha256sum", "-c", checksum.name],  # noqa: S607
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )


def test_the_name_carries_the_version_so_releases_never_share_an_asset(tmp_path: Path) -> None:
    built, _ = archive.build("v1.2.3", tmp_path)

    assert built.name == "opencode-aira-budget-v1.2.3.tar.gz"


@pytest.mark.parametrize("version", ["1.2.3", "v1.2", "v1.2.3-rc1", "v01.2.3", "$(id)"])
def test_a_name_that_is_not_a_release_version_is_refused(tmp_path: Path, version: str) -> None:
    """The name reaches a file name, and the release job passes the tag's."""
    with pytest.raises(ValueError, match="not a release version"):
        archive.build(version, tmp_path)
    assert not list(tmp_path.iterdir())


def test_the_command_writes_both_files_and_refuses_a_bad_name(tmp_path: Path) -> None:
    assert archive.main(["v1.2.3", "--out", str(tmp_path)]) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "opencode-aira-budget-v1.2.3.tar.gz",
        "opencode-aira-budget-v1.2.3.tar.gz.sha256",
    ]
    assert archive.main(["latest", "--out", str(tmp_path / "x")]) == 1
