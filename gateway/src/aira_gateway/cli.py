"""Gateway admin CLI (FRD-101): mint and revoke API keys.

Usage:
  python -m aira_gateway.cli api-key create --subject <id> [--label <text>] [--days <n>]
  python -m aira_gateway.cli api-key revoke --prefix <prefix>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from aira_common.apikeys import KeyLifetimeOutOfRange
from aira_gateway.auth.service import ApiKeyService
from aira_gateway.config import GatewaySettings
from aira_gateway.db.base import build_engine, build_sessionmaker, create_all

#: The exit status of a refused request, the one argparse gives a command line it cannot parse.
_REFUSED = 2


@asynccontextmanager
async def _key_service() -> AsyncIterator[ApiKeyService]:
    """An `ApiKeyService` on a fresh engine, disposed of on the way out."""
    settings = GatewaySettings()
    engine = build_engine(settings.database_url(use_sqlite=settings.uses_sqlite()))
    try:
        # SQLite only, as in the application: Alembic owns the schema of any real database, and
        # this CLI runs from whichever image is at hand during an upgrade (`FRD-114`).
        if engine.dialect.name == "sqlite":
            await create_all(engine)
        async with build_sessionmaker(engine)() as session:
            yield ApiKeyService(session)
    finally:
        await engine.dispose()


async def _create(
    subject: str, label: str | None, days: int | None
) -> tuple[str, str, datetime | None]:
    async with _key_service() as service:
        full, record = await service.create(subject, label, expires_in_days=days)
        return full, record.prefix, record.expires_at


async def _revoke(prefix: str) -> bool:
    async with _key_service() as service:
        return await service.revoke(prefix)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aira-gateway")
    commands = parser.add_subparsers(dest="command", required=True)
    api_key = commands.add_parser("api-key").add_subparsers(dest="action", required=True)

    create = api_key.add_parser("create")
    create.add_argument("--subject", required=True)
    create.add_argument("--label", default=None)
    # Bounded like every key Management issues, by the same rule (`check_key_lifetime`). No
    # `--never-expires`: a credential minted by hand during an incident is the one nobody
    # remembers to take away.
    create.add_argument(
        "--days",
        type=int,
        default=None,
        help="Lifetime in days, 1 to AIRA_API_KEY_MAX_DAYS (default: AIRA_API_KEY_DEFAULT_DAYS).",
    )

    revoke = api_key.add_parser("revoke")
    revoke.add_argument("--prefix", required=True)

    args = parser.parse_args(argv)

    if args.action == "create":
        try:
            full, prefix, expires_at = asyncio.run(_create(args.subject, args.label, args.days))
        except KeyLifetimeOutOfRange as exc:
            print(f"aira-gateway: --days {args.days}: {exc} No key was created.", file=sys.stderr)
            return _REFUSED
        days = args.days if args.days is not None else GatewaySettings().api_key_default_days
        until = f" (until {expires_at.date().isoformat()})" if expires_at else ""
        print(f"API key created (prefix={prefix}), valid for {days} days{until}.")
        print(f"Shown once — store it now:\n{full}")
        return 0

    revoked = asyncio.run(_revoke(args.prefix))
    print("revoked" if revoked else "no active key with that prefix")
    return 0 if revoked else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
