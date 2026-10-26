# ADR-0044 — A person's own budget replaces the one for everybody, under the use case's

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** Vadim Scheibe (owner), with the gateway

## Context

A use case's budgets have had two scopes since 2026-08-14: the **whole use case**, one shared pot,
and **each member**, the same allowance for everybody, counted per person. A third scope bounding
one named person existed until then and was removed on the owner's decision — singling somebody out
did not look tolerant, it needed a row per person kept up to date, and it only ever answered after
somebody had already caused a problem (`FRD-400` §2.1).

That scope was **additive**: every budget that bound a request applied, so a row naming a person
could only ever lower what they had. It was a sanction by construction.

The owner now asks for an individual budget for a specific person, and states the hierarchy it
lives in: the use case's budget first, then the person's own, then the one for everybody. A budget
in that hierarchy is a different thing from the one removed: it **replaces** the default for one
person, so it can raise an allowance — the colleague whose agent runs for the whole team, the
developer evaluating a large model — as readily as it lowers one, and the shared pot still bounds
everybody together.

Two facts of the counters shape the decision:

- A per-person counter is keyed `member:<use case>:<person>`, whichever row bound it
  (`aira_gateway.scopes`). Two rows binding one person in one period would book every request into
  the same counter twice.
- Budgets are per period (`day`, `month`), and a use case may have a per-person budget for each.

## Options considered

- **Re-add the named scope as it was, additive.** Every row binds. Rejected: it can only lower an
  allowance, which is the shape the owner rejected, and with a per-person budget beside it each
  request is booked twice into one counter.
- **An individual budget replaces every per-person budget of that person.** One row decides
  everything about them. Rejected: a monthly exception would silently lift the daily per-person
  limit as well, which nobody asked for.
- **An individual budget replaces the per-person budget of the same period** — chosen.
- **An exemption list** — named people without a per-person limit. Narrower than the request and
  expressible with the chosen option anyway.

## Decision

**Per period, a person's own budget replaces the per-person budget for that person; the use case's
budget binds everybody regardless.** The rule lives in `aira_gateway.scopes.binding`, the one place
a scope is defined, which the budget path and the rate limiter both follow.

Inside that:

1. **On or off.** An individual budget that is switched off refuses nothing, and the person falls
   back to the per-person budget of that period. Switching it off never makes somebody unlimited
   who was limited before — the direction a budget must not be wrong in.
2. **One counter per person and period.** Of a person's own budget and the one for everybody, at
   most one is booked — they share the counter, so the figures a person reads are the same
   whichever row decides, and replacing one by the other mid-period hands out no fresh allowance.
3. **Named by the username the gateway counts by** (`scopes.person`, `ADR-0019`): one budget for a
   person's keys and sign-ins alike. Only somebody who can reach the use case may be named, and a
   name the gateway would read as somebody else is refused (`FRD-613`).
4. **Who sees the name.** The use case's administrators, who set it; the person; and whoever may
   see people. Under strict data mode everybody else reads the row as *another member's*, as they
   read a key's owner (`FRD-627` FR-15). The consumption against it is the person's own figure,
   shown to them alone, as for every per-person budget.
5. **Budgets only.** Rate limits keep their two scopes. The shared rule would carry a per-person
   rate limit the same way, and nothing offers one.

## Consequences

- **Positive.** An exception is a row about one person instead of a change for everybody, and the
  use case's pot still bounds the total. The default stays the one an administrator sets once and
  that keeps applying to whoever joins.
- **Negative.** A row names a person again, so it can go stale when they leave: the console marks a
  row whose person no longer reaches the use case. It is personal data, so it is in the privacy
  notice's register and texts. And the hierarchy has to be visible, or an administrator reading
  *Each member: 20* concludes it binds everybody — so the tab states it, and each person's figures
  name the budget that applies to them.
- **Follow-ups.** None required. An individual rate limit would be one option in the console and one
  in Management's serializer; the gateway already follows the rule.
