# ADR-0016 — Stored prompts are readable, and every read is recorded

> Status: **Accepted** · Date: 2026-08-09 · Owner: Vadim Scheibe
> Amends [`ADR-0009`](ADR-0009-reporting-reads-the-gateway-store.md) · Related: `FRD-406`
> (redaction), `FRD-404` (retention), `FRD-502` (traces), `FRD-505` (this view), `ADR-0013`
> (auditable model access)

## Context

`ADR-0009` shipped aggregate reporting and deferred one thing explicitly:

> **Follow-up**: reporting shows aggregates only. Browsing individual requests would show stored
> prompts to people who are deliberately not members of the use case that produced them, which is
> exactly what content redaction (`FRD-406`, deferred) exists to make safe. That view waits for it,
> and this ADR is the reason the wait is not merely a backlog ordering.

`FRD-406` then arrived in two halves and only one of them shipped. **Credentials are masked** — API
keys, bearer tokens, JWTs, `Authorization:` values, PEM blocks, plus whatever patterns a deployment
adds. **PII deliberately is not**, and that was the right call for a reason that has not changed:
names, customer numbers and prose are *what the payload is stored for*, and a redactor that mangles
them produces payloads nobody can use and a deployment that turns storage off entirely.

So the deferral cannot be discharged the way it was written. The redactor that was supposed to make
this safe will never make it safe in the sense `ADR-0009` meant, because the sensitive content and
the useful content are the same content.

Meanwhile the need got sharper. `FRD-502` gave IT Security a console showing that a caller was
refused four hundred times; `FRD-505` gave it the machine and the credential. The next question is
always the same one, and the console could not answer it: **what did they actually send?**

## Decision

**Stored prompts and responses are readable, by a named set of roles, and every read writes a row
naming who read what, when, and on what authority.**

| Who | May read content | Why |
|---|---|---|
| Global Administrator | yes, recorded | Runs the installation. |
| IT Security | yes, recorded | Investigating an incident without seeing the request is investigating a number. |
| IT Steuerung | **no** | Sees every figure about every use case and no content. Visibility and content are different answers. |
| Use-case administrator | their own use case, recorded | It is their use case's data. |
| Use-case user | their own use case, recorded — or only their own requests, if that use case restricts it | Their own team's traffic, with a switch their administrator owns. |

Three conditions bound it, and none is new: the use case must have **payload storage on**
(`FRD-404`), the payload must not have **expired** under that use case's retention, and the reader
must be inside the caller's visible scope.

### The record is the decision, not a detail of it

`payload_access` is written **before** the content is returned. If recording the read fails, the
read does not happen.

This is what makes the permission grantable at all. `ADR-0009`'s objection was that content would
cross a use-case boundary to somebody outside it — and that objection is *answered by
accountability*, not by masking: the boundary is still crossed, and now it is crossed visibly, by a
named person, at a recorded time, on a stated ground. An access nobody can review is exactly what
the original ADR was protecting against. An access everybody can review is a different act.

It also matches `ADR-0013`: the gateway exists to make model access **auditable**. A console that
reads prompts and leaves no trace would be the one part of the system exempt from the property the
whole system is for.

## Consequences

- **Positive**: the question an incident actually opens with can be answered in the console, by the
  role whose job it is, without asking a member of the use case to forward a screenshot.
- **Positive**: "who has read this customer's prompt" is now a query. It was previously
  unanswerable, because the only way to see a payload was direct database access.
- **Negative**: real personal data is visible to two roles that are not members of the use case. It
  is bounded by storage being on, by retention, by role, and by the record — but it is visible, and
  a deployment that cannot accept that has `store_payloads` per use case (`FRD-404`) as the control
  that makes it impossible rather than merely accounted for.
- **Negative**: `payload_access` grows independently of `request_logs` retention, on purpose. The
  content expires; the fact that somebody read it does not. That table needs a retention decision of
  its own eventually, and it is deliberately not being given one now — a short clock on an access
  log defeats its point.
- **Follow-up**: nothing here alerts. A read is recorded and nobody is told. Whether an unusual
  reading pattern should raise a finding of its own (`FRD-500`'s machinery would fit) is left open
  rather than assumed.
