# FRD-607 — Retiring a use case, and the second decision that removes it

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, stating a regulatory requirement as a threat.
> Related: `FRD-404` (payload retention), `FRD-204` (the read-model), `FRD-122` (audit trail),
> `ADR-0007` (governance is read-only), `ADR-0017` (who creates a use case).

## 1. Problem

> *"Since we work with regulation it would be good if we did not do a full delete but a soft
> delete, and only at some point a full delete after a deliberate decision. Prompts should then
> still be deleted after the defined time — but not in a way that lets somebody use a use case for
> the wrong purposes, compromise it, and delete the use case."*

Two obligations that pull against each other, and the second sentence is the interesting one. It is
not a preference about tidiness; it is a **threat model**, and the system satisfied it exactly
backwards:

- `DELETE /use-cases/<slug>/` was open to a **use-case administrator** — which is to say, to the
  party an investigation would be about.
- It was a hard delete. Django cascaded the memberships and the group grants; the gateway, on
  `usecase.deleted`, removed its own copy of everything including the use-case row.
- The **traffic** survived. `request_logs` are kept on purpose (`FRD-404` §4.1) — and survived
  *context-free*. An audit row names a use case by slug and nothing else. What that slug meant —
  its purpose, its processing notes, which models it had released, whether it stored prompts and
  for how long, who its members were — all lived in Management and went with the row.

So the person best placed to erase the evidence was the person allowed to, and what they erased was
precisely the half that makes the surviving half legible.

## 2. Goals & Non-Goals

**Goals.** Deleting stops the use case completely and destroys nothing. Removing the record is a
separate act by a different role after a waiting period. Stored prompts keep expiring on the
**use case's own** clock throughout.

**Non-goals.** Un-retiring. A use case that was retired and should not have been is re-created by a
Global Administrator under a new slug — reviving one would put the audit history of two different
periods of operation under one name, which is the problem this feature exists to prevent, inverted.

## 3. Functional Requirements

**FR-1 Retire, never remove.** `DELETE` sets `deleted_at` and `deleted_by`. The same
`usecase.deleted` event goes out, so every route to the use case closes exactly as it did before.

**FR-2 Access ends immediately and completely.** API keys deactivated; members, group grants,
budgets, rate limits, anomaly rules, pipeline and usage counters deleted in the gateway.

**FR-3 The request path refuses a retired use case by name**, before anything is spent.

**FR-4 The record survives**, and is visible to governance roles at `GET /use-cases/retired/`.

**FR-5 Purging is a second decision.** `DELETE /use-cases/<slug>/purge/`, **Global Administrator
only**, only for a use case already retired, and only after `PURGE_AFTER_DAYS` (30). It emits
`usecase.purged`, and the gateway then drops its row.

**FR-6 The slug stays taken** until a purge.

**FR-7 Prompts expire on the use case's own period** whether it is live or retired.

## 4. Design

### 4.1 A tombstone is not absence, and that is what makes FR-3 possible

`_retire_usecase` keeps the `use_cases` row. That looks like a detail and is the load-bearing part.

The gateway deliberately has **no** use-case existence check at authentication, and
`_delete_usecase` carried the reason: use cases and API keys arrive on different Kafka topics with
no ordering between them, so a use case whose row has not arrived yet looks exactly like one that
was deleted. Refusing on *absence* would refuse a use case created a second ago.

A tombstone removes that ambiguity. It is positive knowledge that Management retired this slug, and
it can only exist after the use case was known — so a refusal built on it is safe, where a refusal
built on absence was not.

That matters because retirement would otherwise have left a hole exactly where the threat is. API
keys stop working; group grants go with the read-model rows. But a Keycloak group of the form
`/use-cases/<slug>` resolves **from the token alone** (`FRD-102`, `auth/oidc.py`) and touches no
AIRA table. Every OIDC member of a retired use case could have gone on calling it, with all of its
own controls deleted underneath them — no budget, no rate limit, no pipeline, no release.

### 4.2 The retention promise survives the deletion

`retention.py` reads a use case's `retention_days` and `store_payloads` from that same row. While
the row was removed, a retired use case's payloads fell through to the **installation default** —
a different promise, substituted silently at the moment somebody pressed Delete, and wrong in both
directions: a use case that promised 90 days had its prompts destroyed at 7, and one that promised
3 kept them for 30.

Retirement is not consent withdrawn and not consent renewed. The period the data subject was told
about is the period that applies, and `test_a_retired_use_case_keeps_the_period_it_promised` and
`test_a_retired_use_case_is_not_kept_longer_either` pin both directions.

`payloads.py` reads the row too, to tell *never stored* apart from *expired*. That distinction also
silently changed at deletion.

### 4.3 Two roles, because one role is the threat

| | retire | purge |
| --- | --- | --- |
| Use-case administrator | **yes** | no |
| IT Steuerung (governance) | no | **no** — read-only by design (`ADR-0007`) |
| Global Administrator | yes | **yes**, after 30 days |

The waiting period is a **decision gap**, not a retention period. A purge available in the same
minute as the deletion is the same decision with an extra click; 30 days means erasing a record
requires deliberately coming back for it, and that the tombstone was visible to every governance
role for a month while somebody waited.

### 4.4 What Management keeps that it used to cascade away

Memberships, group grants and anomaly rules now survive the retirement. They grant nothing — every
route resolves through a queryset that excludes retired use cases, and the gateway has deleted its
own copies — and they are the record of *who could reach this* and *what it was watched for*, which
is the first thing an investigation asks.

## 5. Testing

Twelve cases in `management/backend/tests/test_soft_delete.py`, five in
`gateway/tests/test_a_retired_use_case_serves_nothing.py`, four in `test_retention.py`, two in
`test_consumer_apply.py`, and four existing tests rewritten where the property genuinely changed.

The ones worth naming because they are not obvious:

- an OIDC caller whose Keycloak group still names the slug is refused — the hole in §4.1;
- ... **before** the rate limiter, proved by giving the use case a limit of one and calling twice:
  if the check ran second, the second call would be a 429 and the allowance of a use case nobody
  may call would have been spent;
- retiring twice emits once and does not overwrite `deleted_by` — at-least-once delivery and a
  double-click are the same shape, and the **first** person to retire a use case is the fact;
- purge at the boundary, retired-a-moment-ago and at exactly 30 days;
- a retired use case is absent from the list, the search, `may_call` **and** every nested route;
- a purged use case falls back to the installation default, which is the honest consequence of
  removing the row that named a shorter one.

Eight mutations (`SD1`–`SD8`). One of them, `SD3`, survived its first form and found a real
problem: the purge guard existed **twice** — a queryset filter and an `if` — so no single mutation
could break it and nothing could say which was load-bearing. That is redundancy rather than defence
in depth, and one copy was deleted.

## 6. Risks

- **A tombstone is personal data too.** `deleted_by` is a username, and the record is kept
  indefinitely until purged. That is the deliberate trade the requirement asks for, and it is
  bounded: prompts — the content — still expire on their own clock. The record is configuration and
  attribution, not content.
- **Storage grows.** A retired use case's row, memberships and grants are small and finite; the
  payloads, which are not, are unaffected.
- **The console has no surface for either action yet.** Deletion has always been API-only, and the
  retired list is proposed as part of the governance overview rather than assumed here.
