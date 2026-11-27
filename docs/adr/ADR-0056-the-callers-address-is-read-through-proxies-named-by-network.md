# ADR-0056 — The caller's address is read through the proxies an installation names, not counted

- **Status:** Accepted
- **Date:** 2026-11-27
- **Deciders:** Vadim Scheibe (owner, by the ticket from Stage T), with Claude
- **Supersedes in part:** `ADR-0018`'s parsing rule (the address read `AIRA_TRUSTED_PROXY_HOPS`
  entries from the right) and `ADR-0007`'s switch (`AIRA_TRUST_FORWARDED_FOR`)

## Context

On Stage T every log line, audit row and failed-sign-in bucket named one Docker-internal address
(`172.18.0.0/24`) instead of the caller's workstation. The two planes read the address two ways:

- **the gateway** recorded the connection's own address unless `AIRA_TRUST_FORWARDED_FOR` was on,
  and then the entry `AIRA_TRUSTED_PROXY_HOPS` places from the right of `X-Forwarded-For`;
- **Management** always read that entry, by DRF's `NUM_PROXIES` (default 1), and took the
  *leftmost* entry of a shorter chain — which the caller wrote.

And no count can be right. A typical installation has two paths:

```
Workstation → the platform's reverse proxy → the console's nginx → gateway / Management
Application → the platform's reverse proxy ──────────────────────→ gateway (/v1/…)
```

Through the console a request passed two proxies that append, straight to the gateway's API one.
A count of one named the platform's proxy for every console user; a count of two let an
application write its own address. So every caller shared one address — and with it one bucket of
the failed-sign-in bound, which a single misconfigured client could empty for the whole console —
and nothing said so.

## Options considered

- **A count per plane** (as before). Wrong on one of the two paths whatever it is set to.
- **uvicorn's `--forwarded-allow-ips`.** Networks rather than a count, but in uvicorn's process
  only: the gateway's tests and Management's Django never see it, an entry that is not an address
  becomes the "client", and only the last of several header lines is read.
- **Networks, read by AIRA on both planes** — chosen. Read from the right: the connection's
  address first; while the installation names an address as its proxy, the entry it appended is
  believed; the first address not named is the caller.
  - **Default: none.** The connection's own address, as both planes recorded behind an unnamed
    proxy before. A default that named private networks (the ticket's example,
    `172.16.0.0/12,10.0.0.0/8`) would let every workstation inside them choose its address, as
    soon as the platform's proxy appends to what the workstation sent — and workstations sit in
    private networks. Docker's own network cannot be named in advance: Compose assigns it.
- **The console's nginx resolving the address too** (`set_real_ip_from`, as the ticket proposed)
  and passing one cleaned entry. Not done: it appends, as before. Collapsing the chain is only
  right if nginx has the same list; a console started without it would hand both services the
  platform proxy's address as the caller, and they could not tell. Reading from the right makes
  whatever a caller wrote unreachable whether nginx cleans it or not.

## Decision

`AIRA_TRUSTED_PROXIES`, one setting for both planes with one default (none), read by one function
(`aira_common.forwarded`): addresses and networks, comma-separated, `172.18.0.0/16,10.20.30.40`.
Every address a plane writes — the audit row, every log line and span, the failed-sign-in bound,
the bound on refusal lines, the anomaly rule that watches for new addresses — is that function's.
An entry that is no address stops a plane at start. `AIRA_TRUST_FORWARDED_FOR` and
`AIRA_TRUSTED_PROXY_HOPS` are read no more; a process that finds either set says so in a
warning at start. DRF reads no header (`NUM_PROXIES = 0`); Management believes a proxy's
`X-Forwarded-Proto` once the list names any.

What a process does is said where an operator looks: its first line names how it reads the
address (`client_address: peer` or `forwarded`) and the networks it trusts, and a connection from a
private address that sends the header unread is a warning, once an hour per address.

## Consequences

- Positive: one address per caller on every path, and no path on which a caller writes it; the
  same rule, setting and default on both planes; a forgotten proxy is visible in the log store.
- Negative: nothing changes until an installation names its proxies — the default records the
  nearest proxy, as before. A list that names the callers' networks lets those callers choose their
  address; the documentation says to name the proxies only.
- An installation that had set the two retired settings records the nearest proxy until it sets
  the new one; its processes say so at start.
- Follow-ups: none planned. `X-Forwarded-Proto` from an unnamed connection is still believed for
  HSTS once the list names any proxy — harmless here, since Management sets no cookie and redirects
  nowhere (`config.security.proxy_ssl_header`).
