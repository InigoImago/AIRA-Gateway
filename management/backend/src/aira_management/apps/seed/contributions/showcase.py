"""A demo somebody can walk through, one role at a time (FRD-130).

`roles_and_users` creates the accounts; this gives each of them something to look at, from the
tables in `showcase_data`.

**Everything goes through the same events the API emits**, with the payload builders imported from
the views: a seed that wrote the tables directly would leave the gateway's read model empty, and
every request against the demo use cases would be refused.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from aira_common.apikeys import NAMESPACE, hash_api_key
from aira_management.apps.anomalies.models import AnomalyRule
from aira_management.apps.anomalies.views import rule_payload
from aira_management.apps.apikeys.models import ApiKey
from aira_management.apps.budgets.models import Budget
from aira_management.apps.catalog.models import Model as CatalogModel
from aira_management.apps.pipelines.models import PipelineConfig
from aira_management.apps.ratelimits.models import RateLimit
from aira_management.apps.seed.contributions.showcase_data import (
    CHAT_MODEL,
    MEMBERSHIPS,
    RELEASES,
    _anomaly_rules,
    _budgets,
    _pipelines,
    _rate_limits,
    _use_cases,
)
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

__all__ = ["CHAT_MODEL", "DEMO_KEY_SALT", "MEMBERSHIPS", "RELEASES", "seed_showcase"]

#: Deterministic API keys are derived from this salt and the slug. **A demo secret on purpose**:
#: anybody reading the repository can compute it, which makes it unmistakable for a real credential
#: and lets the demo be re-run without hunting for the previous run's output. A real key is
#: generated with entropy and shown once. `tools/demo_traffic.py` and its siblings hold the same
#: salt.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"


@register(name="showcase", order=50)
def seed_showcase(fresh: bool) -> SeedResult:
    """Use cases, memberships, budgets, limits, rules, pipelines and one API key each."""
    users = {user.get_username(): user for user in get_user_model().objects.all()}
    if fresh:
        _delete_other_use_cases()

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
        usecase = _seed_use_case(declaration, created)
        _reconcile_memberships(usecase, users, created)
        # One key per use case, owned by its first member, so the demo can call the gateway
        # without anybody minting one first.
        owner_name = MEMBERSHIPS.get(usecase.slug, [("admin", "")])[0][0]
        owner = users.get(owner_name) or users.get("admin")
        if owner is not None:
            keys[usecase.slug] = _ensure_key(usecase, owner, created)

    _seed_budgets(created)
    _seed_rate_limits(created)
    _seed_anomaly_rules(created)
    _seed_pipelines(created)
    # `created` counts things; the plaintext keys are strings. The summary is the mixed thing it is.
    return {**created, "api_keys_plaintext": keys}


def _delete_other_use_cases() -> None:
    """``--fresh``: delete **every** use case the showcase does not declare, and announce it.

    A demo database accumulates the fixtures of every test run pointed at it. Safe because
    `seed_demo` only runs in local or demo mode (`ADR-0007`). The showcase's own slugs are kept:
    deleting one revokes its API keys, revocation is terminal in the gateway's read model, and the
    recreated use case's demo key would answer 401 for ever. Recreating a slug is a reset.
    """
    demo_slugs = {declaration["slug"] for declaration in _use_cases()}
    for stale in UseCase.objects.exclude(slug__in=demo_slugs):
        slug = stale.slug
        with transaction.atomic():
            stale.delete()
            events.emit("usecase.deleted", {"slug": slug})


def _seed_use_case(declaration: dict[str, Any], created: dict[str, int]) -> UseCase:
    """Upsert one use case with what it may call (`FRD-308`, see `RELEASES`)."""
    with transaction.atomic():
        usecase, was_created = UseCase.objects.update_or_create(
            slug=declaration["slug"], defaults=declaration
        )
        created["use_cases"] += int(was_created)
        approved = CatalogModel.objects.filter(approved=True)
        named = RELEASES.get(usecase.slug)
        usecase.allowed_models.set(
            approved.filter(name__in=named) if named is not None else approved
        )
        events.emit("usecase.upserted", _snapshot(usecase))
    return usecase


def _reconcile_memberships(
    usecase: UseCase, users: dict[str, Any], created: dict[str, int]
) -> None:
    """Make the use case's memberships exactly what `MEMBERSHIPS` declares.

    Reconciled, not merely added: a membership the declaration no longer names keeps real
    permissions, and a seed that only adds cannot be re-run to a known state.
    """
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


def _seed_budgets(created: dict[str, int]) -> None:
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


def _seed_rate_limits(created: dict[str, int]) -> None:
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


def _seed_anomaly_rules(created: dict[str, int]) -> None:
    for spec in _anomaly_rules():
        slug = spec.pop("use_case")
        target = UseCase.objects.filter(slug=slug).first() if slug else None
        if slug and target is None:
            # Loudly: a skipped rule would leave a plausible-looking count and a missing rule.
            raise ValueError(
                f"anomaly rule {spec['name']!r} names use case {slug!r}, which this seed does "
                "not create"
            )
        with transaction.atomic():
            # Keyed by (use case, name), as the server upserts, so re-seeding corrects a rule
            # rather than adding a second one beside it (`FRD-208`).
            rule, was_created = AnomalyRule.objects.update_or_create(
                use_case=target, name=spec["name"], defaults=spec
            )
            events.emit("anomaly_rule.upserted", rule_payload(rule))
            created["anomaly_rules"] += int(was_created)


def _seed_pipelines(created: dict[str, int]) -> None:
    for slug, config in _pipelines().items():
        target = UseCase.objects.filter(slug=slug).first()
        if target is None:
            continue
        with transaction.atomic():
            PipelineConfig.objects.update_or_create(use_case=target, defaults=config)
            events.emit("pipeline.upserted", {"use_case": slug, **config})
            created["pipelines"] += 1


def _ensure_key(usecase: UseCase, owner: Any, created: dict[str, int]) -> str:
    """A deterministic key per use case, re-derived rather than regenerated (`DEMO_KEY_SALT`)."""
    # Hex, because the key format says hex: the parts must not contain the separator.
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
