"""Gateway admin CLI (FRD-101): mint and revoke API keys.

Usage:
  python -m aira_gateway.cli api-key create --subject <id> [--label <text>]
  python -m aira_gateway.cli api-key revoke --prefix <prefix>
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from aira_gateway.auth.service import ApiKeyService
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all


def _use_sqlite(settings: GatewaySettings) -> bool:
    return settings.test_database or ("pytest" in sys.modules)


async def _create(subject: str, label: str | None) -> tuple[str, str]:
    settings = GatewaySettings()
    engine = build_engine(settings.database_url(use_sqlite=_use_sqlite(settings)))
    await create_all(engine)
    try:
        async with build_sessionmaker(engine)() as session:
            full, record = await ApiKeyService(session).create(subject, label)
            return full, record.prefix
    finally:
        await engine.dispose()


async def _revoke(prefix: str) -> bool:
    settings = GatewaySettings()
    engine = build_engine(settings.database_url(use_sqlite=_use_sqlite(settings)))
    await create_all(engine)
    try:
        async with build_sessionmaker(engine)() as session:
            return await ApiKeyService(session).revoke(prefix)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aira-gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    api_key = commands.add_parser("api-key").add_subparsers(dest="action", required=True)

    create = api_key.add_parser("create")
    create.add_argument("--subject", required=True)
    create.add_argument("--label", default=None)

    revoke = api_key.add_parser("revoke")
    revoke.add_argument("--prefix", required=True)

    args = parser.parse_args(argv)

    if args.action == "create":
        full, prefix = asyncio.run(_create(args.subject, args.label))
        print(f"API key created (prefix={prefix}). Shown once — store it now:\n{full}")
        return 0

    revoked = asyncio.run(_revoke(args.prefix))
    print("revoked" if revoked else "no active key with that prefix")
    return 0 if revoked else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
