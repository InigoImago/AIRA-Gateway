"""A pattern that refers back to what a group matched is refused on every path that compiles one.

`(.*)\\1!` passed the pattern rule because the rule read a back-reference as consuming nothing, and
it costs time in the cube of the text it reads: 2.8 s on 2 000 characters, on the event loop every
use case shares. A use-case administrator could store it in a pipeline, or post it unsaved to the
dry run, and stop the instance. The rule is `aira_common.patterns`'; this file asks each place
that compiles an operator's pattern whether it applies it — the read-model's injection filter, the
gateway's pipeline check, the dry run and the redactor — because a rule one of them skips is a rule
the installation does not have.

Every assertion uses a text the pattern **matches** at once. Were a pattern compiled, the verdict
would say so in microseconds; a text it fails on would hang the suite instead of failing it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from aira_common.patterns import REFERS_BACK
from aira_gateway.app import create_app
from aira_gateway.auth.keys import DEMO_API_KEY
from aira_gateway.config import GatewaySettings
from aira_gateway.persistence.redaction import RedactionMisconfigured, build_redactor
from aira_gateway.pipeline.classifiers import HeuristicInjectionClassifier, Verdict
from aira_gateway.pipeline.config import problem_in

#: Each shape with a text it matches at once.
REFERENCES = (
    (r"(.*)\1!", "aa!"),
    (r"(a|b)*\1c", "aac"),
    (r"(?P<x>.*)(?P=x)!", "aa!"),
    (r"(a)?(?(1)a*|b*)a*!", "a!"),
)

DRY_RUN = "/v1beta/pipeline:dryRun"


def _filter(pattern: str) -> dict[str, object]:
    return {"steps": [{"type": "injection_filter", "config": {"patterns": [pattern]}}]}


@pytest.mark.parametrize(("pattern", "matched"), REFERENCES)
async def test_the_injection_filter_does_not_compile_a_reference_it_is_handed(
    pattern: str, matched: str
) -> None:
    """The read-model is reachable without Management — over Kafka, from a seed, a direct write —
    so the filter asks again. CLEAN on a text the pattern matches is the pattern not compiled."""
    classifier = HeuristicInjectionClassifier((pattern,), use_builtins=False)

    assert await classifier.verdict(matched) is Verdict.CLEAN


@pytest.mark.parametrize(("pattern", "matched"), REFERENCES)
def test_a_pipeline_carrying_a_reference_is_refused_with_the_rule_s_reason(
    pattern: str, matched: str
) -> None:
    problem = problem_in(_filter(pattern))

    assert problem is not None
    assert problem.startswith("pipeline.steps[0].config.patterns")
    assert REFERS_BACK in problem


@contextmanager
def _dry_run_client() -> Iterator[TestClient]:
    app = create_app(GatewaySettings(auth_required=True, demo_mode=True, log_queue_size=0))
    with TestClient(app, headers={"x-goog-api-key": DEMO_API_KEY}) as client:
        from aira_gateway.db.models import UseCaseRead

        async def seed() -> None:
            async with app.state.db_sessionmaker() as session:
                session.add(UseCaseRead(slug="uc", name="uc"))
                await session.commit()

        client.portal.call(seed)  # type: ignore[union-attr]
        yield client


@pytest.mark.parametrize(("pattern", "matched"), REFERENCES)
def test_the_dry_run_refuses_a_reference_before_any_step_runs(pattern: str, matched: str) -> None:
    """The path the finding named: an unsaved pipeline, posted with a prompt of the attacker's
    choosing. Refused with a `400` naming the rule, so the pattern never meets the prompt."""
    with _dry_run_client() as client:
        response = client.post(
            DRY_RUN, json={"use_case": "uc", "user": matched, "pipeline": _filter(pattern)}
        )

    assert response.status_code == 400, response.text
    assert REFERS_BACK in response.json()["error"]["message"]


def test_a_redaction_pattern_that_refers_back_stops_the_gateway() -> None:
    """The redactor runs over every stored body, and refuses at start-up rather than redact
    with a pattern that can hold the writer: a gateway that does not start says why."""
    with pytest.raises(RedactionMisconfigured, match="refers back to what a group matched"):
        build_redactor(r"secret-(\w+)-\1")
