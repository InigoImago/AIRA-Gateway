"""A demo somebody can walk through, one role at a time (FRD-130).

`seed_demo` already created the five roles and one user each — which lets you *log in* as every
role and see five empty screens. This contribution gives each of them something to look at, and
picks the content so that the differences between the roles are visible rather than described:

    global-admin   sees every use case, and is the only role that may price a model
    it-steuerung   sees every use case and the whole spend report — and may change none of it
    it-security    sees the governance view without the commercial one
    use-case-admin administers two of the three, and cannot see the third at all
    use-case-user  is a member of one, read-only

The third use case exists precisely so that "administers everything" and "administers what they
were given" look different when you switch accounts. A demo where every role sees the same thing
demonstrates nothing.

**Everything goes through the same events the API emits.** A seed that wrote the tables directly
would populate Management and leave the gateway's read model empty — the use cases would appear in
the UI and every request against them would be refused, which is the most confusing possible state
to hand somebody. The payload builders are imported from the views for the same reason: a second
copy of a wire shape is a copy that drifts.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from aira_common.anomalies import RuleAction, RuleKind, RuleTarget
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.views import rule_payload
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.catalog.models import Model as CatalogModel
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.seed.registry import SeedResult, register
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase, UseCaseMembership
from aira_management.apps.usecases.views import (
    _budget_payload,
    _grant,
    _rate_limit_payload,
    _revoke,
    _snapshot,
)

#: The chat model the local endpoint serves. Read from the environment so a deployment that runs a
#: different one still gets a coherent demo rather than three use cases pointing at nothing.
CHAT_MODEL = os.environ.get("AIRA_SEED_LOCAL_CHAT_MODEL", "qwen3:0.6b")

#: Deterministic API keys, derived from a fixed salt and the slug. **A demo secret, and it is one
#: on purpose**: anybody reading this repository can compute it, which is exactly what makes it
#: unmistakable for a real credential. The alternative — a random key shown once — gives a demo
#: that cannot be re-run without hunting for the output of the previous run.
#:
#: This is *not* how a key is issued. That path generates entropy, shows the plaintext once and
#: never again, and is what the UI demonstrates. Saying so here matters more than the code does:
#: a reader who generalises from a seed to the product would generalise the wrong thing.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"


#: Which models each demo use case is released (`FRD-308`). A slug missing from here gets every
#: approved model.
#:
#: **Seed data, not a default.** A use case starts with nothing released and refuses everything —
#: that is the rule, and a demo whose ten requests were all refused would teach it backwards, the
#: way a `block` anomaly rule would (`FRD-500`). A real installation chooses per use case, which is
#: exactly what `entwicklung` shows: narrower than the rest, on purpose.
RELEASES: dict[str, list[str]] = {"entwicklung": [CHAT_MODEL]}


def _use_cases() -> list[dict[str, Any]]:
    """Three use cases, each chosen to make one governance decision visible."""
    return [
        {
            "slug": "kundenservice",
            "name": "Kundenservice",
            "description": (
                "Antworten für den First-Level-Support. Prompts werden gespeichert, damit ein "
                "Vorfall nachvollziehbar bleibt — mit der kürzesten Aufbewahrung, die dafür reicht."
            ),
            "processing_notes": (
                "Kundendaten möglich. Aufbewahrung 7 Tage; Inhaltsmaskierung ist noch nicht "
                "implementiert (FRD-406), was hier bewusst offen dokumentiert ist."
            ),
            "store_payloads": True,
            "retention_days": 7,
        },
        {
            "slug": "entwicklung",
            "name": "Entwicklung",
            "description": (
                "Code- und Recherchefragen aus dem Engineering. Höheres Volumen, deshalb eine "
                "Ratenbegrenzung statt eines engen Budgets."
            ),
            "processing_notes": "Keine personenbezogenen Daten erwartet.",
            "store_payloads": True,
            "retention_days": 30,
        },
        {
            "slug": "coding-assistant",
            "name": "Coding Assistant",
            "description": (
                "Agentische Coding-Unterstützung. **Function Calling ist eingeschaltet** — der "
                "einzige Use Case im Demo, der es braucht, und der Grund, warum der Schalter "
                "standardmäßig aus ist. Eine Anweisung eines Menschen wird hier zu vielen "
                "Modellaufrufen, also sind Limit und Budget dafür bemessen und nicht für einen "
                "Chatbot."
            ),
            "processing_notes": (
                "Quellcode und Dateipfade sind Inhalt: sie stehen in gespeicherten Prompts. "
                "Prompt-Caching ist bewusst **aus** — das lokale Modell meldet keine gecachten "
                "Token, ein eingeschalteter Schalter ohne Wirkung wäre eine Anzeige, die nichts "
                "anzeigt (FRD-125). Einschalten, sobald ein Modell dahintersteht, das cachen kann."
            ),
            "store_payloads": True,
            "retention_days": 7,
            # The whole point of this use case, and the one place in the demo where it is on.
            "tools_enabled": True,
        },
        {
            "slug": "personalwesen",
            "name": "Personalwesen",
            "description": (
                "Textentwürfe für HR. **Speicherung ist abgeschaltet** — die Zahlen werden weiter "
                "erfasst, die Prompts nicht."
            ),
            "processing_notes": (
                "Besondere Kategorien personenbezogener Daten möglich. store_payloads=false: es "
                "wird nichts geschrieben, was später maskiert werden müsste."
            ),
            "store_payloads": False,
            "retention_days": 1,
        },
    ]


#: Who is in what. `ucadmin` deliberately does **not** administer `personalwesen`: switching to that
#: account and finding two use cases instead of three is the fastest way to show that the scoping is
#: real and not a filter in the frontend.
MEMBERSHIPS: dict[str, list[tuple[str, str]]] = {
    "kundenservice": [("ucadmin", UseCaseMembership.ADMIN), ("ucuser", UseCaseMembership.USER)],
    "entwicklung": [("ucadmin", UseCaseMembership.ADMIN)],
    # `ucuser` is a member, and the key is issued to whoever owns the use case — which is the
    # `FRD-604` story the assistant use case exists to make concrete: a name beside an agent's
    # traffic answers "who is accountable for this credential", not "who typed this request".
    "coding-assistant": [("ucadmin", UseCaseMembership.ADMIN), ("ucuser", UseCaseMembership.USER)],
    # Deliberately **not** an oversight role. `itgov` administered this one, which let the demo
    # show a use case `ucadmin` cannot touch — at the cost of teaching the opposite of what the
    # role is: PRD §154 gives IT Steuerung every figure and no write anywhere, and a walkthrough
    # in which it renames a use case demonstrates a boundary that does not exist. The global
    # administrator owns it instead; the point (a use case outside `ucadmin`'s reach) survives.
    "personalwesen": [("admin", UseCaseMembership.ADMIN)],
}


def _budgets() -> list[dict[str, Any]]:
    """A spread across every axis the UI offers, so each control has a live example.

    The figures are **calibrated against what the demo traffic actually costs**, which is the part
    that took a second attempt. A local 0.6B model priced at fractions of a cent per million tokens
    means a plausible-looking monthly cap of €0.50 sits at 0.02% after a walkthrough — a bar that
    is technically correct and shows nothing. These are set so a handful of requests moves each bar
    into the middle of its range, and so that somebody can *reach* a limit by clicking rather than
    take its existence on trust.
    """
    return [
        # Money, monthly, whole use case — the headline control.
        {
            "use_case": "kundenservice",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            # ~40% after one run of `tools/demo_traffic.py`; two more runs reach it.
            "limit_cost": Decimal("0.000300"),
        },
        # A per-member cap under it: one person cannot spend the team's month.
        {
            "use_case": "kundenservice",
            "scope": Budget.MEMBER,
            "subject": "ucuser",
            "period": Budget.DAY,
            "limit_cost": Decimal("0.000100"),
        },
        # Tokens rather than money, for a team that thinks in tokens.
        {
            "use_case": "entwicklung",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            "limit_tokens": 1_200,
        },
        # A request count, which is the one a runaway loop trips first.
        {
            "use_case": "entwicklung",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.DAY,
            "limit_requests": 20,
        },
        {
            # A request count for the assistant, because that is the figure an agent moves
            # fastest and the one a runaway loop trips first. Generous enough that a real session
            # works, small enough that somebody can reach it deliberately.
            "use_case": "coding-assistant",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.DAY,
            "limit_requests": 500,
        },
        {
            "use_case": "personalwesen",
            "scope": Budget.USE_CASE,
            "subject": "",
            "period": Budget.MONTH,
            "limit_cost": Decimal("0.000500"),
        },
    ]


def _rate_limits() -> list[dict[str, Any]]:
    return [
        {
            "use_case": "entwicklung",
            "scope": RateLimit.USE_CASE,
            "subject": "",
            "limit_rpm": 60,
            "burst": 20,
        },
        {
            "use_case": "entwicklung",
            "scope": RateLimit.MEMBER,
            "subject": "ucadmin",
            "limit_rpm": 20,
            "burst": 5,
        },
        {
            # **Sized for an assistant, and that is the lesson.** One human instruction becomes
            # many model calls — a measured OpenCode turn produced three gateway requests for a
            # trivial ask — so a limit calibrated for a chatbot trips in the first minute and the
            # reader concludes the gateway is broken rather than that the limit was wrong. The
            # burst is large for the same reason: an agent arrives in bursts by nature.
            "use_case": "coding-assistant",
            "scope": RateLimit.USE_CASE,
            "subject": "",
            "limit_rpm": 240,
            "burst": 60,
        },
    ]


def _anomaly_rules() -> list[dict[str, Any]]:
    """Rules worth looking at, not rules that fire (`FRD-500`).

    Chosen so the console shows the **range** of the vocabulary and the range of what a rule may
    *do* — one global, three per-use-case, three kinds of target, and both `alert` and `throttle`.

    Every one of them is `alert` or `throttle`, and none is `block`. That is the demonstration:
    `FRD-500` made `alert` the default because a system whose first setting is `block` blocks
    wrongly once and is switched off forever — and a seeded demo that stopped somebody's traffic on
    a first run would teach exactly that lesson the wrong way round.

    Thresholds are set against what the demo traffic actually does, the same calibration
    `FRD-130` had to make for its budgets: a rule that fires on the first request tells a reader
    the system is noisy, and one that can never fire tells them nothing at all.
    """
    return [
        {
            # Global: IT Security's to author, everybody's to read. The one a reader should see
            # first, because it is the one nobody in a use case can turn off.
            "use_case": None,
            "name": "A caller being refused over and over",
            "kind": RuleKind.REFUSAL_RATE,
            "window_minutes": 15,
            "threshold": 40,
            "min_sample": 20,
            "target": RuleTarget.SUBJECT,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "entwicklung",
            "name": "An API key that suddenly costs much more",
            "kind": RuleKind.SPEND_SPIKE,
            "window_minutes": 60,
            # A ratio, not a number: a fixed figure is a budget, and there is one (`FRD-500`).
            "threshold": 300,
            "min_sample": 10,
            "target": RuleTarget.CREDENTIAL,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "entwicklung",
            "name": "A machine nobody has seen before",
            "kind": RuleKind.NEW_SOURCE_IP,
            "window_minutes": 60,
            "threshold": 1,
            "min_sample": 1,
            "target": RuleTarget.SUBJECT,
            "action": RuleAction.ALERT,
        },
        {
            "use_case": "kundenservice",
            "name": "Prompts the filter keeps objecting to",
            "kind": RuleKind.BLOCKED_PROMPT_RATE,
            "window_minutes": 30,
            "threshold": 25,
            "min_sample": 8,
            "target": RuleTarget.SUBJECT,
            # The one rule here that *does* something, and it slows a caller rather than stopping
            # them: a throttle without a rate is not a decision (`FRD-503`).
            "action": RuleAction.THROTTLE,
            "action_minutes": 30,
            "throttle_rpm": 5,
        },
    ]


def _pipelines() -> dict[str, dict[str, Any]]:
    """One heuristic filter and one allow-list, both cheap and both deterministic.

    The LLM classifier is **not** seeded, and that is a demonstration in itself: `FRD-125` §9 —
    against a 0.6B model it answers INJECTION to everything, so a demo configured that way would
    show a filter blocking innocent questions and teach the wrong lesson. The builder offers it,
    the seed does not choose it.
    """
    return {
        "kundenservice": {
            "steps": [
                {
                    "type": "injection_filter",
                    "config": {"mode": "heuristic", "action": "block", "scope": "system_user"},
                }
            ],
            "fallback_models": [],
        },
        # `entwicklung` used to carry an `allow_check` step here. That step is gone (`FRD-308`),
        # and leaving it would have been the worst of both: the gateway drops an unknown step
        # silently, so the demo would show a restriction that does nothing. Its *intent* — one use
        # case deliberately narrower than the others — moved to `RELEASES` below, where it is now
        # enforced at every hop instead of once before routing.
    }


@register(name="showcase", order=50)
def seed_showcase(fresh: bool) -> SeedResult:
    """Use cases, memberships, budgets, limits, pipelines and one API key each."""
    user_model = get_user_model()
    users = {user.get_username(): user for user in user_model.objects.all()}

    if fresh:
        # **Everything**, not just what this contribution made. A demo database accumulates the
        # fixtures of every test run that ever pointed at it — 801 use cases with names like
        # `burst-3i6g5l` on the first walkthrough here — and a global administrator opening a list
        # of those learns nothing except that the list is long.
        #
        # Safe because `seed_demo` refuses to run outside local/demo mode (`ADR-0007`), which is
        # the guard that makes "delete every use case" a reasonable thing for this code to say.
        # The deletions are announced, or the gateway would keep serving read-model rows for use
        # cases Management no longer has.
        #
        # **Only the ones that are not coming back.** Deleting a use case revokes its API keys, and
        # revocation is *terminal* in the read model on purpose (`api_key.created` must never
        # resurrect one). Announcing a delete for a slug this run is about to recreate therefore
        # kills its keys permanently: the demo came back up with three use cases and three keys
        # that answer 401 for ever. Recreating the same slug is a **reset**, not a retirement, and
        # the events have to say so.
        demo_slugs = {declaration["slug"] for declaration in _use_cases()}
        for stale in UseCase.objects.exclude(slug__in=demo_slugs):
            slug = stale.slug
            with transaction.atomic():
                stale.delete()
                events.emit("usecase.deleted", {"slug": slug})

    created = {
        "use_cases": 0,
        "memberships": 0,
        "budgets": 0,
        "rate_limits": 0,
        "anomaly_rules": 0,
        "pipelines": 0,
        "api_keys": 0,
    }
    keys: dict[str, str] = {}

    for declaration in _use_cases():
        with transaction.atomic():
            usecase, was_created = UseCase.objects.update_or_create(
                slug=declaration["slug"], defaults=declaration
            )
            created["use_cases"] += int(was_created)
            # What this use case may call (`FRD-308`). See `RELEASES` for why the demo releases
            # anything at all, and why `entwicklung` gets less than the others.
            approved = CatalogModel.objects.filter(approved=True)
            named = RELEASES.get(usecase.slug)
            usecase.allowed_models.set(
                approved.filter(name__in=named) if named is not None else approved
            )
            events.emit("usecase.upserted", _snapshot(usecase))

        # Reconcile, do not merely add. A membership the declaration no longer names is a ghost
        # with real permissions: `itgov` kept administering `personalwesen` after it was removed
        # from the list above, and `itsec` kept a membership from a run whose declaration is long
        # gone. A seed that only ever adds cannot be re-run to a known state, which is most of
        # what a seed is for.
        declared = {name for name, _ in MEMBERSHIPS.get(usecase.slug, [])}
        undeclared = usecase.memberships.select_related("user").exclude(user__username__in=declared)
        for membership in list(undeclared):
            with transaction.atomic():
                username = membership.user.get_username()
                _revoke(membership.user, usecase)
                membership.delete()
                events.emit("membership.removed", {"slug": usecase.slug, "username": username})

        for username, role in MEMBERSHIPS.get(usecase.slug, []):
            user = users.get(username)
            if user is None:
                continue
            with transaction.atomic():
                UseCaseMembership.objects.update_or_create(
                    use_case=usecase, user=user, defaults={"role": role}
                )
                _grant(user, usecase, role)
                events.emit(
                    "membership.upserted",
                    {"slug": usecase.slug, "username": username, "role": role},
                )
                created["memberships"] += 1

        # One key per use case, owned by its first member, so the demo can call the gateway
        # without anybody having to mint one first.
        owner_name = MEMBERSHIPS.get(usecase.slug, [("admin", "")])[0][0]
        owner = users.get(owner_name) or users.get("admin")
        if owner is not None:
            keys[usecase.slug] = _ensure_key(usecase, owner, created)

    for spec in _budgets():
        target = UseCase.objects.filter(slug=spec.pop("use_case")).first()
        if target is None:
            continue
        with transaction.atomic():
            budget, was_created = Budget.objects.update_or_create(
                use_case=target,
                scope=spec["scope"],
                subject=spec["subject"],
                period=spec["period"],
                defaults=spec,
            )
            events.emit("budget.upserted", _budget_payload(budget, target.slug))
            created["budgets"] += int(was_created)

    for spec in _rate_limits():
        target = UseCase.objects.filter(slug=spec.pop("use_case")).first()
        if target is None:
            continue
        with transaction.atomic():
            limit, was_created = RateLimit.objects.update_or_create(
                use_case=target, scope=spec["scope"], subject=spec["subject"], defaults=spec
            )
            events.emit("ratelimit.upserted", _rate_limit_payload(limit, target.slug))
            created["rate_limits"] += int(was_created)

    for spec in _anomaly_rules():
        slug = spec.pop("use_case")
        target = UseCase.objects.filter(slug=slug).first() if slug else None
        if slug and target is None:
            # Loudly. A silent `continue` here cost this seed its only *acting* rule on the first
            # run — the slug was wrong, three of four rules appeared, and the count looked
            # plausible. The same shape as `record_to_outbox` returning silently for an unknown
            # event type, which this repository has now found three times.
            raise ValueError(
                f"anomaly rule {spec['name']!r} names use case {slug!r}, which this seed does "
                "not create"
            )
        with transaction.atomic():
            # Keyed by (use case, name): the server upserts by name, so re-seeding corrects a rule
            # rather than growing a second one beside it (`FRD-208`).
            rule, was_created = AnomalyRule.objects.update_or_create(
                use_case=target, name=spec["name"], defaults=spec
            )
            events.emit("anomaly_rule.upserted", rule_payload(rule))
            created["anomaly_rules"] += int(was_created)

    for slug, config in _pipelines().items():
        target = UseCase.objects.filter(slug=slug).first()
        if target is None:
            continue
        with transaction.atomic():
            PipelineConfig.objects.update_or_create(use_case=target, defaults=config)
            events.emit("pipeline.upserted", {"use_case": slug, **config})
            created["pipelines"] += 1

    # `created` counts things; the plaintext keys are strings. The union is what the seed
    # framework prints, so it is typed as the mixed thing it is rather than squeezed into ints.
    return {**created, "api_keys_plaintext": keys}


def _ensure_key(usecase: UseCase, owner: Any, created: dict[str, int]) -> str:
    """A deterministic key per use case, re-derived rather than regenerated.

    Deterministic on purpose: a demo that mints a new secret on every run is a demo whose examples
    stop working the second time somebody runs it. This is not how a real key is issued — that path
    shows the plaintext once and never again — and the difference is worth saying out loud rather
    than letting a reader generalise from the seed.
    """
    import hashlib

    from aira_common.apikeys import NAMESPACE, hash_api_key

    # Hex, because the format says hex — the parts must not contain the separator, and a key that
    # only *happens* to parse is a key that stops parsing the day the rule is tightened.
    digest = hashlib.sha256(f"{DEMO_KEY_SALT}:{usecase.slug}".encode()).hexdigest()
    prefix, secret = digest[:8], digest[8:56]
    plaintext = f"{NAMESPACE}_{prefix}_{secret}"
    key_hash = hash_api_key(plaintext)

    with transaction.atomic():
        _, was_created = ApiKey.objects.update_or_create(
            prefix=prefix,
            defaults={
                "use_case": usecase,
                "owner": owner,
                "key_hash": key_hash,
                "label": "showcase",
            },
        )
        events.emit(
            "api_key.created",
            {
                "prefix": prefix,
                "key_hash": key_hash,
                "subject": owner.get_username(),
                "use_case": usecase.slug,
                "label": "showcase",
                "status": "active",
            },
        )
        created["api_keys"] += int(was_created)
    return plaintext
