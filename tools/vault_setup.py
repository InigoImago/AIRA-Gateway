"""Put this installation's secrets into Vault, and give the apps a way to read them (`FRD-116`).

The loader in `aira_common.secrets` reads a KV-v2 path with an AppRole; this script creates the
path, a read-only policy on it and the AppRole, and can copy the credentials in.

**It never prints a secret value.** Not the ones it writes, and not the AppRole's secret-id, which
goes to a file with `0600` because a value on a command line is a value in shell history and in
every process listing on the machine.

**And it never writes the secret-id where git would commit it.** A tracked file goes out with the
next `git commit -a` and an unignored one with the next `git add -A`, so either is refused before
Vault is asked for anything. The default target is ignored by `.gitignore`.

Run it against the local dev Vault with `make vault-init`. Against a real one, set `VAULT_ADDR` and
`VAULT_TOKEN` to a token that may write policies.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import credential_settings
import httpx
import stack_addresses

ROOT = Path(__file__).resolve().parents[1]

#: Everything AIRA reads from Vault — the credential settings of both planes, read off the settings
#: classes (`credential_settings`). Named so `--from-env` cannot quietly copy an unrelated variable
#: in, and so the policy grants exactly what is used.
KNOWN_SECRETS = tuple(sorted(credential_settings.credentials()))

POLICY_NAME = "aira"
ROLE_NAME = "aira"

#: Where the secret-id goes unless told otherwise: inside the checkout, where a Compose override
#: of the installation's own can mount it (no shipped Compose file does —
#: `docs/deployment/integrated.md` §4), and in a directory `.gitignore` excludes, so no commit
#: can carry it.
DEFAULT_SECRET_ID_FILE = ROOT / "deploy" / "compose" / "secrets" / "vault-secret-id"

#: How long a secret-id this script mints stays valid. **Bounded**, because a secret-id that never
#: expires turns one leaked file into read access to every AIRA secret for good. Rotation is a
#: restart (`FRD-116` §5.4): mint a new one with this script before the old one runs out, then
#: restart the services. `0` asks Vault for a secret-id that never expires.
#:
#: There is deliberately **no use count** (`secret_id_num_uses`): every process start is one login,
#: so a use count would make the Nth restart of a healthy deployment fail closed.
DEFAULT_SECRET_ID_TTL = "720h"


def _client(address: str, token: str) -> httpx.Client:
    return httpx.Client(
        base_url=address.rstrip("/"),
        headers={"X-Vault-Token": token},
        timeout=10.0,
    )


def _policy(mount: str, path: str) -> str:
    # Read-only, and on exactly one path. An AppRole that may write its own secrets is an AppRole
    # whose compromise rewrites the installation rather than merely reading it.
    return f'path "{mount}/data/{path}" {{\n  capabilities = ["read"]\n}}\n'


def setup(
    address: str,
    token: str,
    mount: str,
    path: str,
    values: dict[str, str],
    *,
    secret_id_ttl: str = DEFAULT_SECRET_ID_TTL,
) -> dict[str, Any]:
    with _client(address, token) as client:
        mounts = client.get("/v1/sys/mounts").json()
        if f"{mount}/" not in mounts.get("data", mounts):
            client.post(
                f"/v1/sys/mounts/{mount}", json={"type": "kv", "options": {"version": "2"}}
            ).raise_for_status()

        if values:
            # KV-v2 merges nothing: a write replaces the whole secret, so the existing values are
            # read first. Losing a key nobody mentioned on this run would be the worst kind of
            # helpful.
            existing: dict[str, str] = {}
            current = client.get(f"/v1/{mount}/data/{path}")
            if current.status_code == 200:
                existing = current.json()["data"]["data"]
            client.post(
                f"/v1/{mount}/data/{path}", json={"data": {**existing, **values}}
            ).raise_for_status()

        client.put(
            f"/v1/sys/policies/acl/{POLICY_NAME}", json={"policy": _policy(mount, path)}
        ).raise_for_status()

        auths = client.get("/v1/sys/auth").json()
        if "approle/" not in auths.get("data", auths):
            client.post("/v1/sys/auth/approle", json={"type": "approle"}).raise_for_status()

        client.post(
            f"/v1/auth/approle/role/{ROLE_NAME}",
            json={
                "token_policies": [POLICY_NAME],
                "token_ttl": "1h",
                "token_max_ttl": "4h",
                "secret_id_ttl": secret_id_ttl,
            },
        ).raise_for_status()
        role_id = client.get(f"/v1/auth/approle/role/{ROLE_NAME}/role-id").json()["data"]["role_id"]
        minted = client.post(f"/v1/auth/approle/role/{ROLE_NAME}/secret-id").json()["data"]

    return {
        "role_id": role_id,
        "secret_id": minted["secret_id"],
        # Seconds, as Vault reports them for the secret-id it just minted; `0` is "never".
        "secret_id_ttl": int(minted.get("secret_id_ttl") or 0),
        "written": sorted(values),
    }


def committable(path: Path) -> str | None:
    """Why git would commit a file at `path`, or `None` when it would not.

    Tracked is refused because `git commit -a` takes it; untracked and unignored is refused because
    `git add -A` takes it. A path git ignores, or one outside any work tree, is the only safe place
    for a credential that sits next to source code.
    """
    target = path.resolve()
    anchor = target.parent
    while not anchor.exists():
        anchor = anchor.parent

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — a fixed argument list, no shell
            ["git", "-C", str(anchor), *arguments],  # noqa: S607 — `git` from PATH is the intent
            capture_output=True,
            text=True,
        )

    try:
        inside = git("rev-parse", "--is-inside-work-tree")
    except FileNotFoundError:
        # No git on this machine, so nothing here can commit the file either.
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None
    relative = os.path.relpath(target, anchor)
    if git("ls-files", "--error-unmatch", "--", relative).returncode == 0:
        return "git tracks it, so the next `git commit -a` publishes it"
    ignored = git("check-ignore", "-q", "--", relative)
    if ignored.returncode == 1:
        return "it is inside a git work tree and not ignored, so the next `git add -A` takes it"
    if ignored.returncode != 0:
        return f"git could not say whether it ignores it: {ignored.stderr.strip()}"
    return None


def write_secret(path: Path, value: str) -> None:
    """Write `value` to `path` so that no other user can read it at any moment.

    Created `0600` with `O_EXCL` beside the target and moved over it: a file written first and
    chmod'ed afterwards is readable by everyone in between, and an existing file keeps its old mode
    when it is merely truncated.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    # A leftover of this tool's own, from a run under the same process id that died here.
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # The umask may have taken bits away from 0600; this makes the mode exact either way.
            os.fchmod(handle.fileno(), 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _expiry(ttl_seconds: int) -> str:
    if ttl_seconds <= 0:
        return "never — chosen with --secret-id-ttl 0"
    return (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(timespec="minutes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address", default=os.environ.get("VAULT_ADDR") or stack_addresses.url("vault")
    )
    parser.add_argument("--token", default=os.environ.get("VAULT_TOKEN", "root"))
    parser.add_argument("--mount", default=os.environ.get("VAULT_MOUNT", "secret"))
    parser.add_argument("--path", default=os.environ.get("VAULT_PATH", "aira"))
    parser.add_argument(
        "--from-env",
        action="store_true",
        help="copy the credential settings out of this shell's environment into Vault",
    )
    parser.add_argument(
        "--secret-id-file",
        type=Path,
        default=DEFAULT_SECRET_ID_FILE,
        help="where to write the AppRole secret-id (mode 0600); never printed, never a path git "
        "would commit",
    )
    parser.add_argument(
        "--secret-id-ttl",
        default=DEFAULT_SECRET_ID_TTL,
        help="how long the minted secret-id stays valid (a Vault duration; 0 is never)",
    )
    args = parser.parse_args(argv)

    # Before Vault is asked for anything: a secret-id minted and then refused is one more valid
    # credential in the world for no reason.
    refusal = committable(args.secret_id_file)
    if refusal is not None:
        print(
            f"error: refusing to write the secret-id to {args.secret_id_file}: {refusal}.\n"
            "Choose a path outside the checkout or one .gitignore excludes "
            f"(the default is {DEFAULT_SECRET_ID_FILE.relative_to(ROOT)}).",
            file=sys.stderr,
        )
        return 2

    values = {}
    if args.from_env:
        values = {
            name: os.environ[name] for name in KNOWN_SECRETS if os.environ.get(name, "").strip()
        }

    result = setup(
        args.address,
        args.token,
        args.mount,
        args.path,
        values,
        secret_id_ttl=args.secret_id_ttl,
    )
    write_secret(args.secret_id_file, result["secret_id"])

    # Names and locations. The secret-id is on disk with 0600 and is deliberately absent from this
    # output, from any log, and from the shell's history.
    print(
        json.dumps(
            {
                "address": args.address,
                "path": f"{args.mount}/{args.path}",
                "role_id": result["role_id"],
                "secret_id_file": str(args.secret_id_file),
                "secret_id_expires": _expiry(result["secret_id_ttl"]),
                "secrets_written": result["written"],
            },
            indent=2,
        )
    )
    print(
        "\nAdd to deploy/compose/.env:\n"
        f"  VAULT_ADDR=http://vault:8200\n"
        f"  VAULT_ROLE_ID={result['role_id']}\n"
        f"  VAULT_SECRET_ID_FILE=/run/secrets/vault-secret-id\n"
        "\nand mount the file into the app containers. Then remove the plaintext "
        "AIRA_* secrets from that file — leaving both means the environment wins nothing "
        "and proves nothing.\n"
        "\nThe secret-id expires as stated above. Run this again before then and restart the "
        "services: they read it once, at start.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
