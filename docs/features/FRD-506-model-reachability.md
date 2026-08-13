# FRD-506 — Is this model reachable, or only written down?

> Phase: 3 (catalog) · Status: **Done** · Owner: Vadim Scheibe
>
> Related: `FRD-114` (the catalog as a runtime authority), `FRD-117` (diagnostics, and why a health
> check must not wake a model), `ADR-0011` (transport × dialect × identity), `FRD-307` (the
> approved-model catalog, still open).

## 1. Problem

Asked from the running console, and the honest answer was that nothing could tell you:

> *"Wie kann ich neue Modelle definieren von dem Provider, wenn ich keinen Key habe — oder einen
> einfachen Test durchführen, ob es überhaupt ansprechbar wäre?"*

A catalog entry is a **declaration**. It says what a model costs, what it may be asked to do and
what its output cap is, and writing one requires no credential and proves nothing about whether the
model can be reached. An adapter is registered only when *its* credential is configured — so on an
installation without a key for that platform the model sits in the catalog looking perfectly
healthy, and every request for it comes back `model_not_found`.

Which reads, to the person who sent it, as a typo in a model name.

## 2. Requirements

**FR-1 — Three facts, never one.**

| | Means | Fails when |
|---|---|---|
| `declared` | somebody wrote this model down | nobody has; it still serves at the baseline (`FRD-114` FR-7) |
| `served` | an adapter is registered for it | **no credential is configured** |
| `reachable` | the adapter's cheap remote question answered | the network, the endpoint or the credential is wrong |

`reachable: null` means **nothing was contacted** — an adapter with nothing cheap to ask, or one
that was never reached because nothing serves the model. `FRD-117` settled that "we did not look"
and "it is fine" are different answers and only one of them is safe to act on.

**FR-2 — Never a generation.** The check asks the same cheap listing `/readyz` uses. A self-deployed
model can be scaled to zero, and a "does this work" button must not be the thing that wakes it,
bills for it, and takes minutes to answer.

**FR-3 — The upstream's error text is not repeated back.** A provider's message can carry the URL it
was called with, and that URL can carry the key. The exception *type* is diagnostic enough.

**FR-4 — Bounded by role, not by use case.** It describes the *installation*, so: Global
Administrators (who declare models) and IT Security (who investigate why a use case cannot reach
one). Everybody else gets a 403 naming who may.

## 3. Console

**FR-5 — Adding a model requires having *looked*, not having *succeeded*** *(2026-08-09, after the
first version was reported as absent)*.

The button was inside `@if (name())`, so opening "Add model" showed nothing at all and the check
read as missing. A control that appears only once you have done something else is a control nobody
finds. It is always present now, disabled until there is a name.

And Save is unavailable until a check has been **answered** for the name in the form. The
distinction that makes this safe:

| | |
|---|---|
| refusing on a **failed verdict** | would make a fresh installation undeclarable — no credential, no adapter, no model ever declared |
| refusing on **not having asked** | rules out the one outcome a single button can: *"I did not know it was unreachable"* |

A check that **errors** counts as looked-at: a diagnostic that cannot answer must not become a gate,
since the gateway may be down and the catalog is Management's. **Editing is exempt** — correcting a
price on a model that already exists is not the moment to demand a network round trip.

This does not contradict `FRD-114`'s "deprecation warns, revocation blocks". The verdict still only
warns. What is required is the *asking*.


A **Check reachability** button inside the model's declaration panel, with the verdict beside it in
a sentence — "Declared, but nothing serves it" is the one that answers the original question. The
verdict is cleared when another model is opened: a verdict left on screen under a different model is
a wrong answer that looks like a right one.

## 4. What this is not

- **Not** a generation test. Whether a model answers *well* is `FRD-504`'s question, and it costs
  money and time to ask.
- **Not** continuous. `/readyz` already probes in the background per provider; this is a button
  somebody presses while looking at one model.
- **Not** an approval gate. Which models a use case *may* use is `FRD-307`, still open.

## 5. Testing

Nine hermetic cases including the no-credential path and the secret-in-the-error-message case, and
three integration cases against the **real** registry — the only place the distinction means
anything, since the registry is built from the credentials the installation actually has. The live
run confirms both halves: the local model answers, and a Vertex model this stack has no key for
reports "declared, but nothing serves it" rather than looking fine.
