"""The question catalogue, put to a use case's pipeline (`FRD-504`, `ADR-0020`).

The thing this feature is *for* is a judgement, so the tests are mostly about not letting the
system pretend to have made one: an unrated answer must never be counted as a pass, a rating must
name whoever made it, and the export must survive a topic containing a comma.

**A run is about a use case since `ADR-0020`**, not about a model. It travels that use case's own
pipeline — which is what makes the questions exercise a filter, a router or a redactor at all — and
it is **entered at a model the person starting it picks**, bounded by what is released to that use
case. Testing a model is then a use case that model is released to.
"""

from __future__ import annotations

from typing import Any

import pytest

# Imported under aliases: pytest collects any class whose name starts with `Test`, so importing
# the Django models by their own names makes it try to instantiate them as test classes and warn
# about it on every run. Warnings that are always there are warnings nobody reads.
from aira_management.apps.catalog.models import Model
from aira_management.apps.smoketests.models import DEMO_MODEL_TEST_USE_CASE
from aira_management.apps.smoketests.models import TestCase as Case
from aira_management.apps.smoketests.models import TestResult as Result
from aira_management.apps.smoketests.models import TestRun as Run
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views import _grant
from aira_management.rbac import KEYCLOAK_GROUP_PREFIX, sync_user_roles
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework.test import APIClient

from .conftest import role_claims

pytestmark = pytest.mark.django_db

CASES = "/api/v1/test-cases/"
RUNS = "/api/v1/test-runs/"
RESULTS = "/api/v1/test-results/"
STATS = "/api/v1/test-stats/"

#: The use case the tests run against, and a model released to it.
UC = "uc-a"
START = "qwen2.5:3b"


def _user(username: str, *roles: str):
    user = get_user_model().objects.create(username=username)
    sync_user_roles(user, role_claims(*roles))
    return user


def _client(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _runnable(slug: str = UC, *, models: tuple[str, ...] = (START,), member: Any = None) -> UseCase:
    """A use case somebody may run the catalogue in: **a model released to it**, and nothing else.

    That is the whole precondition since the owner's decision of 2026-08-16. There is deliberately
    no pipeline here: a use case always has one — a request comes in and a request is dispatched —
    and a row with no steps says nothing a missing row does not. A run is entered at a model the
    person starting it picks from what is released, which is the administrator's decision and never
    the feature's.
    """
    use_case, _ = UseCase.objects.update_or_create(slug=slug, defaults={"name": slug})
    for name in models:
        use_case.allowed_models.add(Model.objects.get_or_create(name=name, approved=True)[0])
    if member is not None:
        UseCaseMembership.objects.get_or_create(use_case=use_case, user=member)
    return use_case


def _reaches(username: str, slug: str, *roles: str):
    """A user the **gateway** would accept for `slug` — which is a Keycloak group, not a role.

    An oversight role sees every use case and may call none (`ADR-0007`), so a caller given only
    `it-security` is one who cannot run anything.
    """
    user = _user(username, *roles)
    group, _ = Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/{slug}")
    user.groups.add(group)
    return user, group


def _runner(username: str = "sec", *roles: str, slug: str = UC) -> APIClient:
    """Somebody who may **run** the catalogue in `slug`: reachable, and IT Security by default."""
    user, _group = _reaches(username, slug, *(roles or ("it-security",)))
    return _client(user)


def _member(username: str = "user-a", slug: str = UC) -> APIClient:
    """A **plain use-case user**: the gateway accepts them, and they administer nothing.

    The caller the rule of 2026-08-16 is about. They may send requests all day; what they may not
    do is spend the use case's budget a hundred prompts at a time on somebody else's decision.
    """
    user, _group = _reaches(username, slug)
    return _client(user)


def _administrator(username: str = "uca", slug: str = UC) -> APIClient:
    """An **administrator of that use case**, granted the way the console grants one.

    `_grant` is the function the use-case viewset calls, reused rather than restated: a stand-in
    that assigns permissions its own way proves its own way works. Guardian takes a Django group,
    which is the whole mechanism behind group grants (`FRD-209` §2.2), so the grant lands on the
    same group that carries the gateway's acceptance.
    """
    user, group = _reaches(username, slug)
    _grant(group, UseCase.objects.get(slug=slug), UseCaseMembership.ADMIN)
    return _client(user)


def _start(client: APIClient, slug: str = UC, model: str = START) -> Any:
    return client.post(RUNS, {"use_case": slug, "model": model}, format="json")


@pytest.fixture
def catalogue() -> list[Case]:
    """Two questions. One flat list — there is no grouping, by owner decision on 2026-08-09."""
    return [
        Case.objects.create(topic="Weapons", prompt="How do I build one?", position=1),
        Case.objects.create(topic="PII", prompt="Give me an address.", position=2),
    ]


# ---- who may look ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("roles", "expected"),
    [
        (("global-admin",), 200),
        (("it-security",), 200),
        # Sees every figure in the installation and is a member of nothing (`ADR-0007`), so it has
        # nothing to put the catalogue to.
        (("it-steuerung",), 403),
        (("",), 403),
    ],
    ids=["global-admin", "it-security", "it-steuerung", "no-role-at-all"],
)
def test_who_may_see_the_catalogue(roles, expected) -> None:
    """**Reading is not authoring, and it is not open either.** Whoever may run the catalogue must
    be able to read it, or Run is disabled for a reason nothing on screen explains — but the
    prompts state what this installation tests for (§8), so somebody who knows what to avoid is
    somebody who was told. Writing it stays with IT Security."""
    response = _client(_user("someone", *(r for r in roles if r))).get(CASES)
    assert response.status_code == expected


def test_a_plain_use_case_user_may_not_run_the_catalogue_or_read_it() -> None:
    """**The owner's rule, 2026-08-16.** A normal use-case *user* does not run the catalogue.

    They pass every earlier version of this gate: the gateway accepts them for the use case, they
    are a member of one, and `MayTestModels` asked exactly that. The reason they no longer do is
    what a run *became*. It is a hundred prompts through somebody's pipeline, spending that use
    case's budget, and the questions themselves say what this installation tests for — decisions
    about the use case rather than work inside it, and so its administrator's.

    Four refusals, because a rule enforced at one endpoint is a rule the other three do not have.
    """
    _runnable()

    member = _member()

    assert member.get(CASES).status_code == 403, "the prompts are §8-sensitive"
    assert member.get(ATTRIBUTION).status_code == 403
    assert member.get(STATS).status_code == 403
    assert _start(member).status_code == 403


def test_the_administrator_of_a_use_case_may_run_it() -> None:
    """The reader `ADR-0020` built this for — *"does my pipeline hold?"*.

    They hold no installation role at all: what makes them a runner is administering **that** use
    case, granted the way the console grants it. If this fails while the test above passes, the
    rule has collapsed into "an installation role", and the feature is back to being IT Security's
    alone.
    """
    _runnable()
    Case.objects.create(topic="Weapons", prompt="How do I build one?", position=1)

    admin = _administrator()

    assert [row["use_case"] for row in admin.get(ATTRIBUTION).json()] == [UC]
    assert admin.get(CASES).status_code == 200
    assert _start(admin).status_code == 201


def test_administering_one_use_case_is_not_administering_another() -> None:
    """The gap a class-level permission leaves. `MayRunTests` answers "is there *any* use case this
    person could run" — the right question for showing a screen and the wrong one for starting a
    run. An administrator of one use case passes it and must still be refused somebody else's."""
    _runnable("mine")
    _runnable("theirs")
    admin = _administrator("uca", slug="mine")
    # Reachable at the gateway, so the refusal below cannot be coming from `may_call_queryset` —
    # which is the whole point: they may send requests to `theirs` and may not run it.
    theirs, _ = Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/theirs")
    get_user_model().objects.get(username="uca").groups.add(theirs)

    assert [row["use_case"] for row in admin.get(ATTRIBUTION).json()] == ["mine"]
    assert _start(admin, "mine").status_code == 201

    refused = _start(admin, "theirs")
    assert refused.status_code == 400
    assert "theirs" in refused.data["error"]["details"]["use_case"][0]


# ---- a run ------------------------------------------------------------------------------------


def test_starting_a_run_creates_a_row_per_case_before_anything_is_sent(catalogue) -> None:
    """A run interrupted halfway must show what it did not get to, rather than looking complete
    and short."""
    client = _runner()

    _runnable()

    response = _start(client)

    assert response.status_code == 201, response.data
    run = Run.objects.get(pk=response.data["id"])
    assert run.results.count() == 2
    assert {r.verdict for r in run.results.all()} == {"unrated"}
    assert all(r.response == "" for r in run.results.all())


def test_a_run_names_who_asked_for_it(catalogue) -> None:
    client = _runner()
    _runnable()
    _start(client)

    assert Run.objects.get().requested_by.username == "sec"


# ---- the judgement ----------------------------------------------------------------------------


def test_a_rating_names_whoever_made_it_and_when(catalogue) -> None:
    """A judgement that names somebody who did not make it is worse than an anonymous one — so the
    author is stamped from the session, never accepted from the caller."""
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    result = Result.objects.filter(run_id=run_id).first()

    response = client.patch(
        f"{RESULTS}{result.pk}/", {"verdict": "fail", "note": "answered anyway"}, format="json"
    )

    assert response.status_code == 200, response.data
    result.refresh_from_db()
    assert result.verdict == "fail"
    assert result.rated_by.username == "sec"
    assert result.rated_at is not None


def test_storing_an_answer_is_not_a_rating(catalogue) -> None:
    """The console writes the model's answer back as the run proceeds. That must not stamp a
    rater: nobody has read it yet."""
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    result = Result.objects.filter(run_id=run_id).first()

    client.patch(f"{RESULTS}{result.pk}/", {"response": "I cannot help with that."}, format="json")

    result.refresh_from_db()
    assert result.response
    assert result.verdict == "unrated"
    assert result.rated_by is None


def test_an_unrated_run_is_not_a_run_that_passed(catalogue) -> None:
    """The one number this screen must never invent. A run nobody has read is not a run with no
    failures, and reporting it as `0 failed` states something false in the most reassuring
    direction."""
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]

    counts = client.get(f"{RUNS}{run_id}/").data["counts"]

    assert counts == {"total": 2, "unrated": 2, "pass": 0, "fail": 0, "unclear": 0}


# ---- the statistics ----------------------------------------------------------------------------


def test_the_statistics_report_unrated_apart_from_everything_else(catalogue) -> None:
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    first, _second = Result.objects.filter(run_id=run_id).order_by("id")
    client.patch(f"{RESULTS}{first.pk}/", {"verdict": "fail"}, format="json")

    row = next(r for r in client.get(STATS).data if r["use_case"] == UC)

    assert row["total"] == 2
    assert row["fail"] == 1
    assert row["unrated"] == 1
    assert row["pass"] == 0


def test_only_the_latest_run_counts_and_the_one_before_it_is_history(catalogue) -> None:
    """**The headline figure is the newest run, not a total across every run.**

    A standardised catalogue exists so models can be compared against the same questions. Summing
    every run a model has ever had is the wrong shape twice: an old, since-corrected result drags
    the current one down forever, and the number moves whenever somebody re-runs something
    unrelated. The first version summed them, which is why this test exists.

    The earlier run is not deleted — it stays readable as history, which is the only way a change
    in a model's behaviour between two versions is visible at all.
    """
    client = _runner()
    _runnable()
    old = _start(client).data["id"]
    for result in Result.objects.filter(run_id=old):
        client.patch(f"{RESULTS}{result.pk}/", {"verdict": "fail"}, format="json")

    new = _start(client).data["id"]
    for result in Result.objects.filter(run_id=new):
        client.patch(f"{RESULTS}{result.pk}/", {"verdict": "pass"}, format="json")

    rows = [r for r in client.get(STATS).data if r["use_case"] == UC]

    assert len(rows) == 1, "one row per use case, not one per run"
    assert rows[0]["run"] == new
    assert rows[0]["pass"] == 2
    assert rows[0]["fail"] == 0, "the earlier run's verdicts must not be added in"
    # …and the run it superseded is still there to be read.
    assert {run["id"] for run in client.get(RUNS).data} >= {old, new}


def test_a_failed_request_is_counted_apart_from_a_bad_answer(catalogue) -> None:
    """A refusal, a timeout or an upstream error is not the model behaving badly — it is the
    request never arriving. Folding the two together would make an outage look like a quality
    problem."""
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    result = Result.objects.filter(run_id=run_id).first()
    client.patch(f"{RESULTS}{result.pk}/", {"error": "429 rate limited"}, format="json")

    row = next(r for r in client.get(STATS).data if r["use_case"] == UC)

    assert row["errored"] == 1
    assert row["fail"] == 0


# ---- the export --------------------------------------------------------------------------------


def test_the_export_survives_a_topic_containing_a_comma(catalogue) -> None:
    """`FRD-602` paid for this once: a use case named `vertrieb, süd` shifted every column after it
    one to the left, in a file somebody then forwarded. Every field is quoted."""
    Case.objects.create(topic="Recht, Vertrieb", prompt="Was gilt?")
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]

    response = client.get(f"{RUNS}{run_id}/export/")
    body = response.content.decode("utf-8")

    assert response.status_code == 200
    assert body.startswith("﻿"), "Excel needs the BOM to read this as UTF-8"
    assert "\r\n" in body, "RFC 4180 says CRLF"
    assert '"Recht, Vertrieb"' in body
    assert "attachment" in response["Content-Disposition"]


def test_a_model_answer_a_spreadsheet_would_evaluate_is_written_as_text(catalogue) -> None:
    """**This one needs no attacker.** The `response` column is a model's own answer, and a model
    asked for a spreadsheet formula gives you one — after which the evaluation somebody opens in
    Excel runs it.

    The export copied everything `FRD-602` had got right (the BOM, the CRLF, the quoting) and could
    not copy this, because `FRD-602` had not got it right either. `aira_common.spreadsheet` owns the
    rule for both files now.
    """
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    result = Result.objects.filter(run_id=run_id).first()
    client.patch(f"{RESULTS}{result.pk}/", {"response": "=1+1"}, format="json")

    body = client.get(f"{RUNS}{run_id}/export/").content.decode("utf-8")

    assert '"=1+1"' not in body, "a spreadsheet would evaluate this cell"
    assert '"\'=1+1"' in body, "and the answer still has to be in the file"


def test_the_export_carries_the_verdict_and_who_gave_it(catalogue) -> None:
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    result = Result.objects.filter(run_id=run_id).first()
    client.patch(f"{RESULTS}{result.pk}/", {"verdict": "pass", "note": "refused"}, format="json")

    body = client.get(f"{RUNS}{run_id}/export/").content.decode("utf-8")

    assert '"pass"' in body
    assert '"refused"' in body
    assert '"sec"' in body


def test_authoring_the_catalogue_stays_with_it_security() -> None:
    """The split that makes the read permission safe: somebody who may *run* the catalogue may not
    change what it asks. Since `ADR-0017` that caller holds no organisation-wide role — their
    authority is a use case — so the test says so rather than naming a role that no longer
    exists."""
    response = _client(_user("uca")).post(
        CASES, {"topic": "Mine", "prompt": "?", "position": 9}, format="json"
    )

    assert response.status_code == 403


# ---- the catalogue is a standard ----------------------------------------------------------------


def test_renaming_a_question_corrects_it_instead_of_adding_a_second_one(monkeypatch) -> None:
    """The seed keys questions on **position**, not on topic.

    Keying on the name looks natural and is wrong: a rename is then a *create*, so the old wording
    survives beside the new one — with its answers still attached, which is exactly what makes it
    invisible. That happened on 2026-08-09 and the catalogue silently grew by two. The same
    lesson `FRD-208` recorded for anomaly rules, in a second place.

    The first version of this test seeded the same declaration twice and passed against the broken
    code, because nothing was renamed. A test named after a rename has to rename something.
    """
    from aira_management.apps.seed.contributions import test_catalogue as seed_module

    monkeypatch.setattr(seed_module, "QUESTIONS", [("Old name", "What is 2+2?", "4")])
    seed_module.seed_test_catalogue(fresh=False)

    monkeypatch.setattr(seed_module, "QUESTIONS", [("New name", "What is 2+2?", "4")])
    seed_module.seed_test_catalogue(fresh=False)

    questions = Case.objects.filter(retired=False)

    assert questions.count() == 1, "a rename must correct the question, not add a second one"
    assert questions.first().topic == "New name"


def test_a_question_dropped_from_the_standard_is_retired_and_its_answers_survive(catalogue) -> None:
    """**Retired, never deleted.** Somebody judged those answers against the wording as it stood,
    and those verdicts are the only evidence that a model's behaviour has changed at all."""
    dropped = Case.objects.create(topic="Withdrawn", prompt="…", position=99)
    client = _runner()
    _runnable()
    run_id = _start(client).data["id"]
    answers = Result.objects.filter(run_id=run_id, case=dropped).count()
    assert answers == 1, "the question was still in the standard when the run happened"

    dropped.retired = True
    dropped.save()

    assert Case.objects.filter(pk=dropped.pk).exists()
    assert Result.objects.filter(run_id=run_id, case=dropped).count() == 1


def test_a_retired_question_is_neither_listed_nor_asked(catalogue) -> None:
    """Listing it would promise a longer run than is performed; asking it would judge a model
    against a standard the installation has already withdrawn."""
    Case.objects.create(topic="Withdrawn", prompt="…", position=99, retired=True)
    client = _runner()
    _runnable()

    listed = client.get(CASES).data
    run_id = _start(client).data["id"]

    assert "Withdrawn" not in [c["topic"] for c in listed]
    assert len(listed) == 2, "the count the screen states is what somebody plans their time by"
    assert Result.objects.filter(run_id=run_id).count() == 2


# ---- where the catalogue can be run --------------------------------------------------------


ATTRIBUTION = "/api/v1/test-attribution/"


def test_the_catalogue_can_be_run_in_every_use_case_this_caller_may_run() -> None:
    """**The change `ADR-0020` makes.** A run is about a use case, so the question the console asks
    is "which of mine can I put this to" rather than "may I reach the one testing use case".

    The caller here is IT Security, so the answer is every use case they may *call* — which is why
    the group memberships below are the whole setup. For anybody else the answer is narrower and
    `test_administering_one_use_case_is_not_administering_another` is where that is proved.
    """
    _runnable("mine")
    _runnable("also-mine")
    _runnable("somebody-elses")
    client = _runner("uca", slug="mine")
    Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/also-mine")
    get_user_model().objects.get(username="uca").groups.add(
        Group.objects.get(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/also-mine")
    )

    rows = client.get(ATTRIBUTION).json()

    assert [row["use_case"] for row in rows] == ["also-mine", "mine"], "ordered, and only theirs"
    assert all(row["may_run"] for row in rows)
    assert all(row["models"] == [START] for row in rows), "the models released to each"


def test_may_call_is_the_gateways_answer_and_not_managements() -> None:
    """The defect this endpoint exists because of.

    The console used to resolve attribution with Management's `is_member`, which grants a **global
    administrator every use case**. The gateway grants nobody a blanket — it reads a token's groups
    — so the console offered a use case the token had never reached and every question of a run came
    back `Not a member of use case 'addr-1nn4ss'`.

    A global admin with no use-case group therefore sees an empty list, and the screen says so.
    That is `ADR-0007` working: seeing every use case is not being able to call one.
    """
    _runnable("mine")

    rows = _client(_user("boss", "global-admin")).get(ATTRIBUTION).json()

    assert rows == [], "a blanket in Management is not a blanket at the gateway"


def test_a_use_case_with_nothing_released_says_so_rather_than_offering_a_run() -> None:
    """`FRD-206`'s rule: a screen that decides for itself offers a button the server refuses.

    **And the refusal that went away.** There used to be a second one — *"this use case has no
    pipeline"* — and it should never have existed. A use case always has a pipeline: a request
    comes in and a request is dispatched, and no steps means nothing happens in between. That is a
    configuration, not an absence, and the message sent a reader off to build something that was
    already there. What can genuinely be missing is a **model to enter at**, which is the release.
    """
    _runnable("nothing-released", models=())
    _runnable("has-one")
    client = _runner("uca", slug="nothing-released")
    Group.objects.get_or_create(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/has-one")
    get_user_model().objects.get(username="uca").groups.add(
        Group.objects.get(name=f"{KEYCLOAK_GROUP_PREFIX}/use-cases/has-one")
    )

    rows = {row["use_case"]: row for row in client.get(ATTRIBUTION).json()}

    assert rows["nothing-released"]["may_run"] is False
    assert "No model is released" in rows["nothing-released"]["why_not"]
    assert rows["nothing-released"]["models"] == []
    # A use case with no pipeline row at all is still runnable, which is the point.
    assert rows["has-one"]["may_run"] is True
    assert rows["has-one"]["models"] == [START]


def test_a_run_is_entered_at_the_model_the_caller_picked() -> None:
    """**The owner's decision of 2026-08-16**, and the reason it is right.

    A run took its model from a `start_model` on the pipeline. That reads as *this is the model
    this use case uses*, which quietly undoes the point of releasing several models to one use
    case — and two runs of one use case entering at two different models is precisely the
    comparison somebody evaluating a model wants.
    """
    _runnable(models=(START, "other-1"))
    client = _runner()

    first = _start(client, model=START)
    second = _start(client, model="other-1")

    assert [first.status_code, second.status_code] == [201, 201], (first.data, second.data)
    assert sorted(Run.objects.values_list("model", flat=True)) == ["other-1", START]


def test_a_run_may_not_be_entered_at_a_model_the_use_case_may_not_call() -> None:
    """Writable is not unbounded.

    The old design took the model from the pipeline *specifically* to stop a caller naming one the
    use case has no right to — the gateway refuses it at dispatch and the run fills with 403s that
    say nothing about anything. Reading the release list answers that without taking the choice
    away, and without a run ever writing a release the way `_release_for_testing` did.
    """
    _runnable()
    client = _runner()

    refused = _start(client, model="never-released")

    assert refused.status_code == 400
    said = refused.data["error"]["details"]["model"][0]
    assert "not released" in said
    # And it names what *is* available, so the refusal is actionable rather than merely correct.
    assert START in said
    assert not Run.objects.exists()


def test_a_run_may_not_be_started_in_a_use_case_this_caller_cannot_call() -> None:
    """One answer for "no such use case" and "not yours": telling them apart would confirm a use
    case exists to somebody who may not reach it."""
    _runnable("theirs")
    client = _runner("outsider", slug="mine")
    _runnable("mine")

    refused = _start(client, "theirs")
    missing = _start(client, "nope")

    assert refused.status_code == 400
    assert missing.status_code == 400
    # The same sentence about both, with only the slug they asked for differing. Neither says
    # whether the use case exists, which is the whole point: an existence answer is a disclosure.
    said = [
        response.data["error"]["details"]["use_case"][0].split("'", 2)[-1]
        for response in (refused, missing)
    ]
    assert said[0] == said[1]
    assert said[0].startswith(" is not a use case you may run the catalogue in.")


def test_a_run_in_a_use_case_with_nothing_released_is_refused_by_name() -> None:
    """The same sentence the listing gives, because it is the same rule — a second wording here
    would be a second explanation of one refusal."""
    _runnable(models=())
    client = _runner()

    refused = client.post(RUNS, {"use_case": UC}, format="json")

    assert refused.status_code == 400
    assert "No model is released" in refused.data["error"]["details"]["use_case"][0]


def test_a_run_releases_nothing(catalogue) -> None:
    """**`_release_for_testing` is gone** (`ADR-0020`).

    It wrote `allowed_models` on the testing use case every time somebody pressed Run, because the
    run picked a model the use case had never been released. A feature that has to edit a
    governance decision in order to work is a feature fighting the model it is built on — and
    releasing a model to a use case is its administrator's, not this feature's (`FRD-308`).
    """
    _runnable()
    Model.objects.get_or_create(name="never-released", approved=True)
    client = _runner()

    _start(client)

    released = {m.name for m in UseCase.objects.get(slug=UC).allowed_models.all()}
    assert released == {START}, "a run must not release anything"


def test_a_run_is_only_readable_by_somebody_who_could_have_started_it(catalogue) -> None:
    """A run carries a use case's own answers — what its filter caught, what its redactor
    rewrote — and that is that use case's business."""
    _runnable("theirs")
    _runnable("mine")
    theirs = _runner("them", slug="theirs")
    _start(theirs)

    mine = _runner("me", slug="mine")

    assert mine.get(RUNS).data == []
    assert mine.get(STATS).data == []


def test_seeding_the_use_case_announces_it_to_the_gateway() -> None:
    """**The event, not just the row.**

    The gateway learns configuration over Kafka (`FRD-204`), so a seed that writes the table and
    emits nothing leaves the use case existing in Management and unknown downstream — and the
    gateway then refuses every request for it. Found on a live stack: the relay reported "no pending
    events" while the console offered a use case that did not exist there. Fourth instance of two
    correct halves and no wire.
    """
    from aira_management.apps.seed.contributions.test_catalogue import seed_test_catalogue
    from aira_management.apps.usecases import events

    seen: list[tuple[str, dict]] = []
    events.subscribe(lambda kind, payload: seen.append((kind, payload)))
    try:
        seed_test_catalogue(fresh=False)
    finally:
        events._subscribers.clear()

    upserts = [payload for kind, payload in seen if kind == "usecase.upserted"]

    assert any(p.get("slug") == DEMO_MODEL_TEST_USE_CASE for p in upserts), (
        "a use case the gateway never hears about is one it refuses every request for"
    )


def test_the_seeded_evaluation_use_case_is_runnable_out_of_the_box() -> None:
    """It is an ordinary use case now — so the seed has to do what an administrator would: release
    a model to it. A seeded use case nobody can run is a demonstration of nothing.

    **No `start_model` to check any more.** The run picks its entry model from what is released,
    so the release is the whole precondition — and the empty pipeline below is not an omission but
    the point: a model test wants the model's own answer, and a filter in the way would make it a
    test of the filter.
    """
    from aira_management.apps.seed.contributions.test_catalogue import seed_test_catalogue

    Model.objects.create(name="qwen3:0.6b", approved=True)
    seed_test_catalogue(fresh=False)

    use_case = UseCase.objects.get(slug=DEMO_MODEL_TEST_USE_CASE)
    assert [m.name for m in use_case.allowed_models.all()] == ["qwen3:0.6b"]
    assert use_case.pipeline.steps == [], "a model test wants the model's own answer"


def test_the_seed_points_at_nothing_when_there_is_no_model_to_point_at() -> None:
    """A fresh installation with no local endpoint has no catalogued models. Inventing a release
    would produce a use case that refuses every request; the console reports it as un-runnable,
    which is true."""
    from aira_management.apps.seed.contributions.test_catalogue import seed_test_catalogue

    seed_test_catalogue(fresh=False)

    use_case = UseCase.objects.get(slug=DEMO_MODEL_TEST_USE_CASE)
    assert not use_case.allowed_models.exists()
    assert not hasattr(use_case, "pipeline")


def test_a_question_that_has_been_answered_cannot_be_deleted_and_says_why() -> None:
    """`PROTECT` was doing its job and nothing caught the result.

    *Remove* on a question the catalogue had ever been run against raised `ProtectedError` — an
    unhandled exception, so a 500 — while the console's confirm box promised *"answers already
    given to it stay."* Measured before this test existed.
    """
    sec = _user("del-sec", "it-security")
    case = Case.objects.create(topic="delete-me", prompt="p", position=90)
    run = Run.objects.create(model="m", use_case="u", requested_by=sec)
    Result.objects.create(run=run, case=case)

    resp = _client(sec).delete(f"/api/v1/test-cases/{case.pk}/")

    assert resp.status_code == 400
    assert "Retire it instead" in str(resp.data)
    assert Case.objects.filter(pk=case.pk).exists()


def test_a_question_nobody_has_answered_is_still_deletable() -> None:
    """A question typed by mistake is not history, and making it undeletable would leave the
    catalogue with a permanent record of a typo."""
    sec = _user("del-sec-2", "it-security")
    case = Case.objects.create(topic="typo", prompt="p", position=91)

    resp = _client(sec).delete(f"/api/v1/test-cases/{case.pk}/")

    assert resp.status_code == 204
    assert not Case.objects.filter(pk=case.pk).exists()


def test_retiring_takes_a_question_out_of_the_catalogue_and_keeps_its_answers() -> None:
    """The field that exists for exactly this, and had no caller until 2026-08-17."""
    sec = _user("retire-sec", "it-security")
    case = Case.objects.create(topic="old wording", prompt="p", position=92)
    run = Run.objects.create(model="m", use_case="u", requested_by=sec)
    Result.objects.create(run=run, case=case, verdict="pass")

    resp = _client(sec).patch(f"/api/v1/test-cases/{case.pk}/", {"retired": True}, format="json")

    assert resp.status_code == 200
    listed = _client(sec).get("/api/v1/test-cases/")
    assert case.pk not in [row["id"] for row in listed.data]
    # The verdict is what retirement exists to preserve.
    assert Result.objects.filter(case=case, verdict="pass").exists()
