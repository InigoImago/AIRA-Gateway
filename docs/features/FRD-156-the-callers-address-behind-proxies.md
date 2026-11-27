# FRD-156 — The caller's address behind proxies, one rule on both planes

> Phase: 6 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: a ticket from Stage T (vux8092) on v0.1.17, priority high — *"Client-Adresse
> hinter Proxys einheitlich und sicher ermitteln"*: every line named `172.18.0.0/24`, the
> Docker-internal address of a proxy, instead of the workstation's.
>
> Related: `ADR-0056` (this decision), `ADR-0018` and `ADR-0007` (which it supersedes in part),
> `FRD-103` FR-3 (the audit row's address), `FRD-405` and `ADR-0015` (the failed-sign-in bound),
> `FRD-631` FR-9 (how a line writes an address), `FRD-501` (the anomaly rule for new addresses).

## 1. Summary

Both planes now read the caller's address one way: from the right of `X-Forwarded-For`, as far as
the proxies `AIRA_TRUSTED_PROXIES` names vouch for it — addresses or networks, one setting for
both, empty by default. The first address the list does not name is the caller, so the console's
path (two proxies) and an application's straight to the gateway (one) name the same workstation,
and a caller that connects from an address the list does not name is recorded as that address,
whatever its header says. Each process says in its first line how it reads the address, and warns
when a private address keeps sending a header it does not read.

## 2. Goals & Non-Goals

**Goals**
- The same, real client address on both paths, in the log, the audit trail and the failed-sign-in
  bound.
- No caller chooses its address — not one that connects directly with a header of its own, not one
  behind a proxy that appends to it.
- One setting with one default on both planes.
- A process says how it reads the address, and a forgotten proxy is visible.

**Non-Goals**
- **Naming the proxies for an installation.** Only the installation knows which addresses are its
  proxies (`ADR-0056`): the default trusts none, and a list wider than the proxies lets the callers
  inside it choose their address.
- **The console's nginx resolving the address.** It appends, as before; why is `ADR-0056`.
- The `Forwarded` header (RFC 7239). No proxy of a known installation writes it.

## 3. User Stories
- As **IT Security**, I want every audit row and line to name the workstation a request came
  from, on whichever path, so that an investigation and the anomaly rule for new addresses see
  callers rather than one proxy.
- As **a user of the console**, I want another client's wrong credentials not to lock me out.
- As **an operator**, I want the first line of each process to say how it reads the address, and a
  warning when a proxy of mine is not named.

## 4. Functional Requirements
- **FR-1 One setting.** `AIRA_TRUSTED_PROXIES`, on the gateway and Management alike: addresses and
  networks, comma-separated, e.g. `172.18.0.0/16,10.20.30.40`; empty, the default, names none. An
  entry that is neither refuses the plane's start, naming the entry.
- **FR-2 One reading** (`aira_common.forwarded`). Start at the connection's address. While the
  list names it, step to the next entry from the right of `X-Forwarded-For` — every header line, in
  order. The first address the list does not name is the caller; if every entry is named, the
  leftmost. An entry with a port or in brackets is its address; one that is no address ends the
  reading at the proxy that wrote it. An IPv4 address inside IPv6 is the IPv4 address.
- **FR-3 Every address is this one.** The gateway's `client_ip` and Management's `source_of` are
  the reading, and every address either plane writes is theirs: the audit row, the access line, a
  refusal's line and its per-source bound, the spans, the failed-sign-in bound and, through the
  audit row, the anomaly rule for new addresses. DRF reads no header of its own (`NUM_PROXIES = 0`).
- **FR-4 Visible.** `process_started` states `client_address` — `peer` with no proxy named,
  `forwarded` with some — and `trusted_proxies`, the networks. A connection from a private address
  that sends `X-Forwarded-For` while the list does not name it is a warning, `forwarded_for_ignored`,
  once an hour per address, at most 256 addresses remembered.
- **FR-5 The old settings say they are gone.** `AIRA_TRUST_FORWARDED_FOR` and
  `AIRA_TRUSTED_PROXY_HOPS` are read no more; a process that finds either in its environment writes
  `setting_retired` with what replaced it. No start is refused for them.
- **FR-6 Compose passes the list to both planes** — before, Management never received the hop
  count, and kept its default behind any proxy of an installation's own.
- **FR-7 HSTS.** Management believes a proxy's `X-Forwarded-Proto` once the list names any proxy.

## 5. Design & Architecture

`aira_common.forwarded.TrustedProxies` parses the list once per value (`trusted_proxies`, cached)
and answers `client(peer, forwarded_for)`. The gateway reads its setting per request from the app's
settings, so two apps in one process read their own lists; Management reads its one.

The example chain of the ticket, with Docker's network `172.18.0.0/16`, where the platform's proxy
reaches the published ports as `172.18.0.1` and the console's nginx is `172.18.0.5`:

| Path | Connection | `X-Forwarded-For` | Caller |
|---|---|---|---|
| console | `172.18.0.5` (nginx, named) | `10.1.2.3, 172.18.0.1` | `10.1.2.3` |
| API | `172.18.0.1` (the proxy, named) | `10.1.2.3` | `10.1.2.3` |
| direct, header forged | `10.9.9.9` (not named) | `203.0.113.7` | `10.9.9.9` |
| console, header forged | `172.18.0.5` | `203.0.113.7, 10.1.2.3, 172.18.0.1` | `10.1.2.3` |

With `AIRA_TRUSTED_PROXIES=172.18.0.0/16`. Were `10.0.0.0/8` in the list as well, the last row
would answer `203.0.113.7`: the workstation is in a named network, so what it wrote is believed.

## 6. Data Model
None. `request_logs.source_ip` holds the caller's address where it held the proxy's.

## 7. API / Interface Contract
- `AIRA_TRUSTED_PROXIES` on both planes (`docs/CONFIGURATION.md`, `docs/DEPLOYMENT.md` §3.7,
  `docs/INTEGRATIONS.md` §8). `AIRA_TRUST_FORWARDED_FOR` and `AIRA_TRUSTED_PROXY_HOPS` removed.
- `process_started`: `client_address`, `trusted_proxies`. New warnings: `forwarded_for_ignored`
  (`source_ip` as `AIRA_TELEMETRY_IP` writes it, `hint`), `setting_retired` (`setting`, `hint`).

## 8. Security & Privacy
- A caller connecting from an address the list does not name cannot choose its address; one behind
  a named proxy cannot either, because what it wrote lies left of an entry a named proxy appended.
- The list names proxies. A network of callers in it lets them choose their address — the
  documentation says so where the setting is described, and the table in §5 shows it.
- The audit trail now holds workstations' addresses where it held a proxy's, which is what the
  privacy notice already said it records ("source IP address"); logs and spans write them as
  `AIRA_TELEMETRY_IP` says, a pseudonym and the network by default (`FRD-631` FR-9). The notice
  does not change.

## 9. Observability
`process_started` and the two warnings (FR-4, FR-5); `docs/LOGGING.md` lists them.

## 10. Testing & Acceptance Criteria
- `libs/tests/test_the_caller_is_read_through_the_named_proxies.py` — the reading: both paths,
  forged headers, every entry named, ports, brackets, IPv6, entries that are no address, several
  header lines, the list's spelling, the warning and its bounds.
- `gateway/tests/test_the_callers_address_behind_proxies.py`, `management/backend/tests/
  test_the_callers_address_behind_proxies.py` — **the ticket's acceptance**: through the console
  and straight to the API, one address in the access line, the audit row and the failed-sign-in
  bucket; a direct caller's header is never its address.
- The tests of the failed-sign-in bounds, the recorder and the first line, rewritten for the rule.
- `tools/tests/test_both_planes_read_the_callers_address_alike.py` — one name, one default, and
  Compose hands the list to both planes.
- Mutations `XFF-1` – `XFF-20`; `W2`, `GS-43`, `DA-14`, `FXM-M1-5`, `FXM-M1-9` and `DTR15`
  re-anchored onto the rule that replaced theirs.

**Acceptance (the ticket's):**
- *Über die Konsole und direkt auf `/v1/...` steht dieselbe, echte Client-Adresse in Log,
  Audit-Trail und Sperre nach ungültigen Zugangsdaten (Test für beide Wege)* — FR-2, FR-3.
- *Ein Aufrufer, der direkt (ohne Proxy) einen eigenen `X-Forwarded-For` sendet, kann seine Adresse
  nicht wählen (Test)* — FR-2.
- *Gateway und Management haben dieselbe Einstellung mit denselben Standardwerten* — FR-1, FR-6.
- *`process_started` nennt die Art der Adressermittlung* — FR-4.

## 11. Dependencies & Risks
- **Nothing changes until an installation names its proxies.** Stage T needs
  `AIRA_TRUSTED_PROXIES` set to the Docker network its proxy and the console reach the services
  from — `172.18.0.0/16` by its lines — and the platform's proxy's own address where Docker shows
  that instead.
- An installation that had set the retired settings records the nearest proxy until it sets the new
  one; its processes warn at start.

## 12. As built (2026-11-27)

Built as specified. Measured on the three images built from this tree, on a network of their own
(`172.21.0.0/16`, Docker's gateway `172.21.0.1`), the console's nginx in front, a request sent from
the Docker host with `X-Forwarded-For: 10.1.2.3` as a platform proxy on that host would:

| `AIRA_TRUSTED_PROXIES` | Request | Recorded |
|---|---|---|
| `172.21.0.0/16` | through the console to the gateway, and straight to it | `10.1.2.3` both |
| `172.21.0.0/16` | through the console to Management (`auth_refused`, `http_request`) | `10.1.2.3` |
| nginx and Docker's gateway, `/32` | from a container nobody named, `X-Forwarded-For: 203.0.113.7`, straight and through the console | the container's `172.21.0.5` both |
| — | through the console, and straight | nginx's `172.21.0.4`, Docker's `172.21.0.1`; one `forwarded_for_ignored` each |

Each process's first line said `client_address` and the networks; one started with
`AIRA_TRUSTED_PROXY_HOPS=2` wrote `setting_retired`.
