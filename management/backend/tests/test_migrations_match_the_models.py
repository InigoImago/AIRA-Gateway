"""The migrations describe the models, and nothing has drifted from them.

Found on 2026-08-12 by running `makemigrations --check` for an unrelated reason: `usecases`
had a **pending** `AlterField` on `allowed_models` that nobody had written. `FRD-308` changed the
field's declaration and the migration for it was never generated, so the recorded schema and the
models had been disagreeing since that feature shipped.

Nothing was broken at the time, which is why it survived: the difference did not change how the
column stores anything, so every test and the whole running stack were fine. What it does instead
is quieter and lands on somebody else:

- a fresh `migrate` builds a schema Django considers out of date, so the *next* difference is
  discovered on top of an already-wrong baseline;
- the next person to touch an unrelated model runs `makemigrations`, and this alteration is swept
  silently into **their** migration, where a reviewer reads it as part of their change;
- and the day a drift *does* matter — a column that really needs altering — there is no signal
  left to notice it by, because the check has been red all along.

A model change without its migration is the same shape as a topic with no emitter and a capability
in a hand-written list that nobody compares against the constant: two halves that are each correct
and no wire between them. This is the wire.
"""

from __future__ import annotations

import io

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_no_model_change_is_missing_its_migration() -> None:
    """`makemigrations --check` as a test rather than as a step somebody remembers.

    `--dry-run` so it reports rather than writes: a guard that repairs what it finds would hide
    the drift it exists to surface, and the repair belongs in a commit with a name on it.
    """
    out = io.StringIO()
    try:
        call_command("makemigrations", "--check", "--dry-run", stdout=out, stderr=out)
    except SystemExit:  # Django exits non-zero when something is pending
        pytest.fail(
            "the models have changed without a migration:\n"
            + out.getvalue()
            + "\nRun `uv run python management/backend/manage.py makemigrations` and commit the "
            "result. Left alone this ends up inside somebody else's unrelated change."
        )
