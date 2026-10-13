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
from aira_management.apps.seed.registry import SeedResult, register, seeded_use_cases
from aira_management.apps.usecases import events
from aira_management.apps.usecases.models import UseCase
from aira_management.apps.usecases.views import (
    _budget_payload,
    _rate_limit_payload,
    _revoke,
    _snapshot,
)
from aira_management.apps.usecases.views.grants import _admit
from aira_management.apps.usecases.views.lifecycle import purge, retire
from aira_management.apps.usecases.views.payloads import _api_key_payload, _pipeline_payload

__all__ = [
    "CHAT_MODEL",
    "DEMO_KEY_SALT",
    "MEMBERSHIPS",
    "RELEASES",
    "reissued_prefixes",
    "seed_showcase",
]

#: Deterministic API keys are derived from this salt and the slug. **A demo secret on purpose**:
#: anybody reading the repository can compute it, which makes it unmistakable for a real credential
#: and lets the demo be re-run without hunting for the previous run's output. A real key is
#: generated with entropy and shown once. `tools/demo_traffic.py` and its siblings hold the same
#: salt.
DEMO_KEY_SALT = "aira-showcase-demo-not-a-secret"


def _declared_slugs() -> list[str]:
    return [declaration["slug"] for declaration in _use_cases()]


def _key_holders(slug: str) -> list[tuple[str, str]]:
    """Who gets a demo key on ``slug``, first member first — the global administrator where the
    declaration names nobody."""
    return MEMBERSHIPS.get(slug, [("admin", "")])


@register(name="showcase", order=50, use_cases=_declared_slugs)
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
        # **One key per member**, not one per use case. It was one, owned by the first member, and
        # that made the first member the only person with traffic anywhere in the demo: `ucuser`,
        # whose whole role is *uses a use case*, had no requests at all and saw an empty "What you
        # used" card. It also left every per-person figure — `FRD-606`'s table, the per-head budget
        # on `kundenservice` — with exactly one row to show, which is a comparison with nothing to
        # compare.
        #
        # The first member keeps the **owner-agnostic** derivation, because printed examples,
        # `showcase_agent.py` and `showcase_try_it.py` all re-derive that one by slug alone
        # (`FRD-130` FR-4); the others are derived with their own name.
        for index, (member_name, _role) in enumerate(_key_holders(usecase.slug)):
            owner = users.get(member_name)
            if owner is None:
                continue
            keys[f"{usecase.slug}/{member_name}"] = _ensure_key(
                usecase, owner, created, named=index > 0
            )

    _seed_budgets(created)
    _seed_rate_limits(created)
    _seed_anomaly_rules(created)
    _seed_pipelines(created)
    # `created` counts things; the plaintext keys are strings. The summary is the mixed thing it is.
    return {**created, "api_keys_plaintext": keys}


def _delete_other_use_cases() -> None:
    """``--fresh``: delete **every** use case no seed contribution declares, and announce it.

    A demo database accumulates the fixtures of every test run pointed at it. Safe because
    `seed_demo` only runs in local or demo mode (`ADR-0007`). **Every contribution's** slugs are
    kept (`registry.seeded_use_cases`), not only this one's: deleting one revokes its API keys,
    revocation is terminal in the gateway's read model, a retired use case stays a tombstone no
    upsert revives, and the contribution that recreates it would leave it dead at the gateway.
    Recreating a slug is a reset.

    A deletion here removes the row, so it is a retirement **and** a purge, through the very
    helpers the API and the purge command use: the gateway ends access and then drops the
    tombstone, the object permissions naming the use case go with it, and a later use case of the
    same name is not born retired.
    """
    for stale in UseCase.objects.exclude(slug__in=seeded_use_cases()):
        with transaction.atomic():
            retire(stale, by="seed_demo --fresh")
            purge(stale)


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
            # The view's own sequence: the row, exactly this role's permissions, the event.
            _admit(usecase, user, role)
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
            pipeline, _made = PipelineConfig.objects.update_or_create(
                use_case=target, defaults=config
            )
            events.emit("pipeline.upserted", _pipeline_payload(slug, pipeline))
            created["pipelines"] += 1


def _ensure_key(usecase: UseCase, owner: Any, created: dict[str, int], *, named: bool) -> str:
    """A deterministic key, re-derived rather than regenerated (`DEMO_KEY_SALT`, `FRD-130` FR-4).

    ``named`` puts the owner's username into the digest, which is what gives a second member of the
    same use case a second key. The first member's key stays derived from the slug alone: every
    printed example and both hand-over scripts re-derive *that* one, and a demo that mints a new
    secret is a demo whose documentation stops working on the second run.
    """
    prefix, plaintext = _demo_key(usecase.slug, owner.get_username(), named=named)
    key_hash = hash_api_key(plaintext)

    with transaction.atomic():
        key, was_created = ApiKey.objects.update_or_create(
            prefix=prefix,
            defaults={
                "use_case": usecase,
                "owner": owner,
                "key_hash": key_hash,
                "label": "showcase",
            },
        )
        events.emit("api_key.created", _api_key_payload(key))
        created["api_keys"] += int(was_created)
    return plaintext


def _demo_key(slug: str, owner: str, *, named: bool) -> tuple[str, str]:
    """The ``(prefix, plaintext)`` of a demo key — derived, never generated (`DEMO_KEY_SALT`)."""
    # Hex, because the key format says hex: the parts must not contain the separator.
    material = f"{DEMO_KEY_SALT}:{slug}"
    if named:
        material = f"{material}:{owner}"
    digest = hashlib.sha256(material.encode()).hexdigest()
    prefix, secret = digest[:8], digest[8:56]
    return prefix, f"{NAMESPACE}_{prefix}_{secret}"


def reissued_prefixes() -> frozenset[str]:
    """The prefixes of every demo key this contribution derives, and so reissues on every run.

    Asked by a ``--fresh`` reset before it deletes people: revocation is terminal at the gateway,
    so announcing one of these revoked would make the demo key answer 401 for ever after the
    showcase hands it out again under the same prefix.
    """
    return frozenset(
        _demo_key(slug, member, named=index > 0)[0]
        for slug in _declared_slugs()
        for index, (member, _role) in enumerate(_key_holders(slug))
    )
