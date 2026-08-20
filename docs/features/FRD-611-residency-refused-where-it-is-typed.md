# FRD-611 — A region the policy forbids is refused where it is typed

> Phase: 6 (governance) · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner, after reading an audit trail: *"`global` out of allowed regions, and refuse
> the ones that are not permitted in the console."*
> Related: `ADR-0012` §6 (one residency policy, one owner), `FRD-609` (several regions),
> `FRD-115` (Vertex), `FRD-506` (inform, never block), `FRD-507` (the editor's provenance).

## 1. Problem

Residency has always been enforced — `aira_gateway.residency` checks every address against
`AIRA_ALLOWED_REGIONS`, and a configured model outside the list stops the gateway from starting.
Enforced **at the moment a request is addressed**, which is correct and weeks late: a model is
catalogued in Management, and whoever catalogued it hears nothing until a caller receives a 4xx.

Two consequences of that gap, both measured on the running installation:

- `global` sat in this deployment's `AIRA_ALLOWED_REGIONS` — added for Google AI Studio, whose key
  was empty, so it bought nothing — and the audit trail showed **two requests already processed
  there**. `global` names no region and guarantees none, which under a DSGVO requirement is the
  one value that must not be permitted by accident.
- A model was catalogued at a region this installation does not permit, and the catalogue said
  nothing about it.

## 2. Decision

**Refuse an impermissible region in the console, as it is typed — and keep exactly one owner for
the policy.**

Management holds **no copy** of the allow-list. Residency is one policy with one owner
(`ADR-0012` §6), and a second copy is how two planes come to disagree about what an installation
may do — the shape this repository has already paid for twice (`LESSONS.md` §1). The gateway
publishes the list on the answer the console *already* fetches when the model editor opens: both
facts are used in the same breath — *which provider* and *where* — so a second request would be a
second thing to fail.

### 2.1 An older gateway that says nothing is not an empty allow-list

The edge case worth the most thought. A gateway that predates this change sends no `allowedRegions`
field at all. A console reading that absence as an **empty** allow-list would refuse every region
somebody typed, on every model, during a rolling update (`FRD-127`) — turning a missing field into
a total outage of the editor.

**Absent and empty are different answers**, and only one of them is a policy. Where the field is
missing the console declines to have an opinion and the gateway refuses at request time exactly as
before: informing where it can, never blocking on its own ignorance.

### 2.2 A text field with suggestions, not a dropdown

Offered as a `datalist` rather than a `<select>`, for two reasons that are both about being wrong
less often: the field is legitimately **empty** for a platform addressed by model name alone, and
somebody who has just widened the policy on the gateway should be able to type the new region
before this console has been reloaded.

## 3. What it changes for a reader

| | before | now |
| --- | --- | --- |
| a forbidden region | catalogued silently; a caller's 4xx, later | red the moment the row is opened, Save disabled |
| what is allowed | not visible anywhere in the console | named in the field's own error |
| `global` in this deployment | permitted, and used twice | not permitted |

**The consequence is stated rather than worked around.** `gemini-3.5-flash` is reachable with this
installation's credential **only** at `global` — measured across five endpoints — so under an EU
residency requirement it is not usable here yet. It stays in the catalogue, unapproved and refused
where it is typed, because a catalogue that hides a model somebody asked for answers the question
*"why can I not use it"* with silence.

## 4. Testing

Three on the gateway (the list is published; an unset one falls back to the EU defaults; `global`
appears only where it is configured) and six in the console — including the absent-field case
**twice over**, because one condition guards both *"no region typed"* and *"no list published"* and
only two separate mutations tell those apart. Seven were broken by hand and seen to fail, plus the
`V21` mutation.

Verified in a browser against the running stack: the catalogued `gemini-3.5-flash` row shows its
`global` in red the moment it is opened, names what is allowed, and Save is disabled until the
region is one of them.

## 5. Risks

- **A policy widened on the gateway is not visible until the editor is reopened.** Accepted: the
  list arrives with the provider answer the editor already fetches, and a poll would be a second
  thing to fail for a value that changes when somebody restarts the gateway.
- **The console can only inform.** It is not the enforcement point and must not be read as one —
  the gateway refuses at the moment it addresses a request, whatever any console believes.
