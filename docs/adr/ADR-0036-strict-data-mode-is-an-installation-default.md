# ADR-0036 — Strict data mode is an installation default, decided on read

- **Status:** Accepted
- **Date:** 2026-10-15
- **Deciders:** Vadim Scheibe

## Context
A system *objectively capable* of monitoring employees' conduct or performance is co-determined
(`BetrVG` §87(1) no. 6). AIRA's per-use-case content ladder (`FRD-622`) let each use case's
administrator choose how much colleagues see, and its strictest rung still showed every request row
with name, key, time and latency, every finding that named a person, and — to `report.read_all` —
every figure split by person. A works council cannot rely on a guarantee any administrator can
lift, and one that leaks through the rows was not a guarantee.

## Options considered
- **Narrow the per-use-case `oversight` rung further.** Keeps the choice with the use case, so the
  assurance is only as good as every administrator's restraint.
- **A console switch for the Global Administrator.** One person can switch monitoring on; the
  works council would have to watch the switch.
- **An installation setting, on by default, decided on read.** The operator who deploys sets it;
  stored per-use-case settings are left untouched and simply not honoured while it is on.

## Decision
The third. `AIRA_STRICT_DATA_MODE`, shared by both planes, default `true`. Each plane applies it
when it reads — the gateway's visibility decisions, Management's serializer, role editor and
privacy notice — rather than rewriting stored configuration, so switching it off is lossless and an
event from an older Management cannot weaken it. While it is on, seeing a person (content, a split
by person, a name or key on a request, a person a finding names) is reserved to the two roles that
handle incidents, Global Administrator and IT Security: `payload.read_any` and
`incident.investigate` cannot be granted to any other role.

## Consequences
- Positive: one guarantee, stated in the privacy notice, that holds by configuration.
- Negative / trade-offs: an installation that wants a use case's members to read each other's
  prompts, or IT Steuerung to see figures per person, must turn the mode off for the whole
  installation. Latency per person is gone for everybody, IT Security included.
- Telemetry and logs leave AIRA's role checks behind, so the mode reaches them differently: a
  person's name there is a keyed pseudonym (`AIRA_PSEUDONYM_SECRET`), and re-identifying a caller
  goes through the audit trail, which the incident roles read. Key prefix and source address stay.
