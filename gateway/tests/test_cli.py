import pytest

from aira_gateway import cli


def test_cli_create_prints_key(capsys) -> None:
    exit_code = cli.main(["api-key", "create", "--subject", "cli-user", "--label", "svc"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "aira_" in output
    assert "prefix=" in output


def test_cli_revoke_missing_prefix(capsys) -> None:
    exit_code = cli.main(["api-key", "revoke", "--prefix", "doesnotexist"])
    assert exit_code == 1
    assert "no active key" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("days", "named"),
    [("0", "at least 1"), ("-5", "at least 1"), ("181", "180 days")],
    ids=["zero", "negative", "past-the-maximum"],
)
def test_the_break_glass_key_is_bounded_like_every_key(days: str, named: str, capsys) -> None:
    """`ADR-0015`: neither plane can mint an unbounded key — the CLI included. Zero and a negative
    number became "never expires", and a large one a key that outlives everybody who knew of it,
    each reported as "valid for <n> days"."""
    exit_code = cli.main(["api-key", "create", "--subject", "operator", "--days", days])

    captured = capsys.readouterr()
    assert exit_code != 0
    assert named in captured.err
    assert "aira_" not in captured.out, "a key was minted anyway"


def test_the_default_lifetime_is_stated_with_its_date(capsys) -> None:
    from datetime import UTC, datetime, timedelta

    assert cli.main(["api-key", "create", "--subject", "operator"]) == 0

    output = capsys.readouterr().out
    expected = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    assert "valid for 30 days" in output
    assert expected in output


async def test_the_cli_never_builds_a_schema_beside_the_migrations(monkeypatch) -> None:
    """Alembic owns any real database. `create_all` from an image older or newer than its
    migrations resurrects a dropped table or front-runs a new one (`FRD-114`), which is why the app
    runs it on SQLite only — and the CLI ran it everywhere."""
    from aira_gateway.config import GatewaySettings

    calls: list[str] = []

    async def _recording(engine) -> None:  # noqa: ANN001
        calls.append(engine.dialect.name)

    monkeypatch.setattr(cli, "create_all", _recording)
    monkeypatch.setattr(GatewaySettings, "uses_sqlite", lambda self: False)
    async with cli._key_service():
        pass
    assert calls == []

    monkeypatch.setattr(GatewaySettings, "uses_sqlite", lambda self: True)
    async with cli._key_service():
        pass
    assert calls == ["sqlite"]


async def test_create_all_refuses_a_database_the_migrations_own() -> None:
    from aira_gateway.db.base import build_engine, create_all

    engine = build_engine("postgresql+psycopg://u:p@127.0.0.1:1/none")
    try:
        with pytest.raises(ValueError, match="migrations"):
            await create_all(engine)
    finally:
        await engine.dispose()
