from aira_management.config.app_settings import ManagementSettings
from aira_management.config.database import build_databases


def test_build_databases_sqlite() -> None:
    dbs = build_databases(ManagementSettings(), use_sqlite=True)
    assert dbs["default"]["ENGINE"] == "django.db.backends.sqlite3"
    assert dbs["default"]["NAME"] == ":memory:"


def test_build_databases_postgres() -> None:
    dbs = build_databases(ManagementSettings(postgres_db="aira_mgmt"), use_sqlite=False)
    default = dbs["default"]
    assert default["ENGINE"] == "django.db.backends.postgresql"
    assert default["NAME"] == "aira_mgmt"
    assert default["PORT"] == "5432"
