"""The first-time Postgres init hands the Keycloak password to SQL as data.

`CREATE ROLE keycloak … PASSWORD '${KC_DB_PASSWORD}'` spliced the shell's value into the
statement, so a password containing `'` ended the literal early: `ERROR: unrecognized role option`,
`ON_ERROR_STOP` aborted the init, and the cluster came up without a Keycloak database. Measured
against a throwaway `postgres:17-alpine` with the password `it's a $secret; ok`: the old script
exited 3; this one creates the role, which then logs in with exactly that password.

Checked here on the text, because the property is the *shape* of the hand-over — a psql variable,
quoted by psql, in a heredoc the shell does not expand.
"""

from __future__ import annotations

from pathlib import Path

INIT = Path(__file__).resolve().parents[2] / "deploy/compose/postgres/init/01-init-databases.sh"


def test_the_password_is_a_psql_variable_and_not_spliced_text() -> None:
    script = INIT.read_text()

    assert '-v kcpass="$KC_DB_PASSWORD"' in script
    assert "PASSWORD :'kcpass'" in script
    assert "${KC_DB_PASSWORD}" not in script.partition("<<")[2], "the shell splices it into SQL"
    assert "<<-'SQL'" in script, "an unquoted heredoc lets the shell expand what psql should read"
