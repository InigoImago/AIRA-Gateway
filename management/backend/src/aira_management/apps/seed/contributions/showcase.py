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

from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
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
        "entwicklung": {
            "steps": [{"type": "allow_check", "config": {"models": [CHAT_MODEL]}}],
            "fallback_models": [],
        },
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
