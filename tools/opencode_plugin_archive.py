"""Pack the OpenCode budget plugin as an archive a release carries.

The plugin (`tools/opencode/plugins/aira-budget/tui.tsx`) is installed by putting one file where
OpenCode looks. An installation that rolls it out to workstations — Terraform, Ansible, a login
script — needs it at an address that does not move and needs no credentials: a file attached to the
GitHub Release, downloadable by anybody for a public repository. A package registry would be the
other way, and GitHub's npm registry requires a token even to install a public package.

The archive is laid out **relative to OpenCode's configuration directory** (`~/.config/opencode`),
so unpacking it there is the installation:

    plugins/aira-budget/tui.tsx

It carries no `tui.json`. OpenCode 1 needs one naming the plugin, but that file is the developer's
own — themes, keybindings, other plugins — and an archive that unpacks over it would erase them.
Writing that one entry is the rollout's job, and the runbook says what it is.

**Reproducible**: the same plugin and version give the same bytes — fixed owner, mode and time, a
gzip header without a timestamp or name — so the published checksum can be recomputed from a
checkout, and a rollout that compares checksums does not reinstall an unchanged file.

    uv run python tools/opencode_plugin_archive.py v0.1.7 --out dist/
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import sys
import tarfile
from pathlib import Path

from release_version import parse

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tools" / "opencode" / "plugins" / "aira-budget" / "tui.tsx"

#: Where the file goes, relative to OpenCode's configuration directory. OpenCode 2 discovers a
#: plugin directory under `plugins/` by itself; OpenCode 1 loads what `tui.json` names.
MEMBER = "plugins/aira-budget/tui.tsx"

#: The file's mode once unpacked: readable by everybody, as a configuration file is.
MODE = 0o644

#: A fixed modification time, 2000-01-01T00:00:00Z. Zero would be just as reproducible, but some
#: unpackers warn about a file dated 1970.
MTIME = 946_684_800


def name(version: str) -> str:
    """The archive's file name: the version is in it, so a release's assets never collide."""
    return f"opencode-aira-budget-{version}.tar.gz"


def packed(source: bytes) -> bytes:
    """The archive's bytes for one plugin text."""
    info = tarfile.TarInfo(MEMBER)
    info.size = len(source)
    info.mode = MODE
    info.mtime = MTIME
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(info, io.BytesIO(source))
    gzipped = io.BytesIO()
    # `filename=""` and `mtime=0`: gzip writes the source's name and the time into its header
    # otherwise, and two builds of one version would differ in exactly those bytes.
    with gzip.GzipFile(filename="", mode="wb", fileobj=gzipped, mtime=0) as stream:
        stream.write(tar_bytes.getvalue())
    return gzipped.getvalue()


def build(version: str, out: Path) -> tuple[Path, Path]:
    """Write the archive and its checksum file into `out`; return both paths.

    The checksum file is in `sha256sum` format, so `sha256sum -c` checks a download beside it.
    """
    if parse(version) is None:
        raise ValueError(f"'{version}' is not a release version (vX.Y.Z)")
    out.mkdir(parents=True, exist_ok=True)
    archive = out / name(version)
    archive.write_bytes(packed(SOURCE.read_bytes()))
    checksum = out / f"{archive.name}.sha256"
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum.write_text(f"{digest}  {archive.name}\n")
    return archive, checksum


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", help="the release tag, e.g. v0.1.7")
    parser.add_argument("--out", type=Path, default=ROOT / "dist", help="directory to write into")
    arguments = parser.parse_args(argv)
    try:
        paths = build(arguments.version, arguments.out)
    except ValueError as problem:
        print(problem, file=sys.stderr)
        return 1
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
