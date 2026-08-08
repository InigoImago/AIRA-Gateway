# ADR-0015 — A convenience default is a production default, one variable away

* **Status**: Accepted
* **Date**: 2026-08-08
* **Supersedes / amends**: extends `ADR-0007` (hardening) to the data plane

## Context

`ADR-0007` hardened the management plane: it refuses to boot outside `environment=local` while
any development default is still in place. The gateway — the half that serves the traffic, holds
the upstream credentials and writes the audit trail — read `environment` for its telemetry and
acted on it nowhere.

A full read of the code after four weeks of work on authentication, roles and group grants found
the gap that mattered most somewhere else entirely, and it was found by *sending a request*, not
by reading: the KIRA compatibility surface asked

```python
if memberships and header not in memberships:   # refuse
```

An **empty** membership list therefore meant "anything goes" rather than "nothing". A caller who
belonged to no use case at all could send `X-AIRA-Use-Case: somebody-elses`, receive a real
answer, and have the tokens billed to that use case's budget and written into its audit trail. The
Gemini surface refused the identical request, in both of its selector forms. Proven live against
the running stack; `request_logs` showed the row, attributed to the victim.

The two facts belong in one decision because they have one cause. Both are a rule that exists
correctly in one place and is *restated* in another — `ADR-0007`'s environment check restated
nowhere for the gateway, the membership rule restated by hand on the second surface. This project
has recorded that shape repeatedly (`FRD-126`'s pre-dispatch order, `FRD-206`'s permission
predicates, `FRD-602`'s export scope). It keeps arriving because a restatement is cheap to write
and invisible when it drifts.

## Decision

**1. One rule, one function, per question — and the surfaces differ only in their envelope.**
`use_case_refusal(principal, use_case)` returns a *reason* or `None`; `authorize_use_case` wraps
it in a Gemini error, the KIRA surface wraps it in a KIRA error. A surface that wants to authorise
calls the function; there is no second rule to keep in step. The deliberate exception survives
inside it: an **unbound** API key (the CLI break-glass credential, minted by an operator with
database access) stays unrestricted, because it exists for the moment the control plane is gone.

**2. The gateway refuses to start when its environment contradicts its settings.**
`aira_gateway.security` mirrors `management/config/security.py`: outside `local`, open routes, the
published Postgres password and OIDC-without-an-audience each stop the process, naming every
reason at once rather than one restart at a time.

The check is **environment-shaped, not stricter defaults**. `make up`, the demo, the published
demo key and a laptop's zero-configuration start are unchanged — and `AIRA_DEMO_MODE` exempts a
deployment outright, because "this is a demo" is a loud, deliberate declaration and a hosted demo
has to be able to exist. A hardening pass that breaks the demo is a hardening pass that gets
reverted.

**3. Absence of a claim is not a claim that passed.** PyJWT verifies `exp` when it is *present*
and accepts a token carrying none. `exp`, `iat` and `sub` are now required. The audience stays
optional in the verifier and required by deployment (decision 2), so a laptop keeps working
against a realm with no audience mapper while production cannot reach that state by accident.

**4. The verdict is public; the diagnosis is not.** `/readyz` stays unauthenticated — a probe
carries no credential and a readiness endpoint answering 401 reports every pod unhealthy — but the
body naming the database host, the Kafka host, every upstream and the current fallbacks is served
only to an authenticated caller, and to everyone locally.

**5. A credential is redacted everywhere it is written, not only where we remembered.** `?key=`
has been kept out of exported spans since `ADR-0007`; the web server's own access log recorded the
request line verbatim, which is the *more* widely readable of the two. Stored payloads likewise
now pass a credential-shaped-pattern redactor (`FRD-406`), narrow on purpose: names, numbers and
prose are the work, and a redactor that mangles them produces payloads nobody uses and a
deployment that turns storage off.

**6. A control that needs a verified identity cannot bound a caller who has none.** Every limit
`FRD-405` built is keyed by use case or member. Authentication *failures* are now bounded per
source address — counting refusals only, so a working credential never touches the bucket and the
bound can be low enough to be worth having.

## Consequences

- A deployment that was relying on `AIRA_AUTH_REQUIRED=false` outside `local` will not start, and
  will say which settings and which environment. That is the intent.
- A token minted without `exp`/`iat`/`sub` stops being accepted. Keycloak sends all three.
- The console asks `/me` for the key policy rather than carrying its own copy, so a form states the
  numbers the server enforces.
- Operators reading `/readyz` for diagnosis must present a credential, or read it from a local
  run. The status code and `status`/`degraded` are unchanged for probes.
- **Every API key is bounded.** `AIRA_API_KEY_DEFAULT_DAYS` (30) applies when the issuer names no
  lifetime; `AIRA_API_KEY_MAX_DAYS` (180) is the ceiling, and asking for more is refused **by name**
  rather than silently shortened. Neither plane can mint an unbounded key — not Management's API,
  not the gateway's break-glass CLI.

  The first version of this made the expiry *optional*, arguing that "an expiry which cannot be
  omitted is one an operator sets to the year 3000". That argument is about the **maximum**, not
  about the default, and using it to justify an opt-in confused the two: the default decides
  whether anybody has to remember, and nobody does. A ceiling is what stops the year 3000; a
  default is what stops the credential that outlives its reason. The answer is both.

  Two exceptions, each stated rather than implied. **Keys issued before this** carry NULL and keep
  working: expiring them would be an outage decided on the operator's behalf, and a silent one —
  nothing tells an integration why it stopped. The console marks them "no end date" so they are
  visible rather than merely tolerated. And the **demo key** does not expire, the same exemption
  the deployment guard makes: its plaintext is published in this repository, its security property
  comes from `AIRA_DEMO_MODE` being a declared state, and a demo that dies a month after somebody
  clones the repository is a demo nobody trusts.
- Twenty mutations (`H1`–`H20`) reintroduce each fix. `A4` was **re-anchored**: the JWT options
  became a multi-line dict, and a mutation whose anchor has moved protects nothing.
