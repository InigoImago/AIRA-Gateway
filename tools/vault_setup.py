"""Put this installation's secrets into Vault, and give the apps a way to read them (`FRD-116`).

The mechanism has existed since 2026-08-06 and the running stack used none of it, because nothing
created the path, the policy or the AppRole and nothing passed `VAULT_ADDR` to a container. This
script is the missing half.

**It never prints a secret value.** Not the ones it writes, and not the AppRole's secret-id, which
goes to a file with `0600` because a value on a command line is a value in shell history and in
every process listing on the machine.

Run it against the local dev Vault with `make vault-init`. Against a real one, set `VAULT_ADDR` and
`VAULT_TOKEN` to a token that may write policies, and pass `--no-dev`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import stack_addresses

#: Everything AIRA reads from Vault. Named here so `--from-env` cannot quietly copy an unrelated
#: variable in, and so the policy grants exactly what is used.
KNOWN_SECRETS = (
    "AIRA_GOOGLE_API_KEY",
    "AIRA_POSTGRES_PASSWORD",
    "AIRA_SECRET_KEY",
    "AIRA_VERTEX_SERVICE_ACCOUNT_JSON",
    "AIRA_AZURE_API_KEY",
    "AIRA_OPENAI_API_KEY",
)

POLICY_NAME = "aira"
ROLE_NAME = "aira"


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
    address: str, token: str, mount: str, path: str, values: dict[str, str]
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
            json={"token_policies": [POLICY_NAME], "token_ttl": "1h", "token_max_ttl": "4h"},
        ).raise_for_status()
        role_id = client.get(f"/v1/auth/approle/role/{ROLE_NAME}/role-id").json()["data"]["role_id"]
        secret_id = client.post(f"/v1/auth/approle/role/{ROLE_NAME}/secret-id").json()["data"][
            "secret_id"
        ]

    return {"role_id": role_id, "secret_id": secret_id, "written": sorted(values)}


def main() -> int:
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
        help="copy the known AIRA_* secrets out of this shell's environment into Vault",
    )
    parser.add_argument(
        "--secret-id-file",
        default="deploy/compose/.vault-secret-id",
        help="where to write the AppRole secret-id (mode 0600); never printed",
    )
    args = parser.parse_args()

    values = {}
    if args.from_env:
        values = {
            name: os.environ[name] for name in KNOWN_SECRETS if os.environ.get(name, "").strip()
        }

    result = setup(args.address, args.token, args.mount, args.path, values)

    secret_file = Path(args.secret_id_file)
    secret_file.parent.mkdir(parents=True, exist_ok=True)
    secret_file.write_text(result["secret_id"])
    secret_file.chmod(0o600)

    # Names and locations. The secret-id is on disk with 0600 and is deliberately absent from this
    # output, from any log, and from the shell's history.
    print(
        json.dumps(
            {
                "address": args.address,
                "path": f"{args.mount}/{args.path}",
                "role_id": result["role_id"],
                "secret_id_file": str(secret_file),
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
        "and proves nothing.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
