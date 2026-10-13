# ADR-0035 — Windows sign-on is Keycloak's, and AIRA takes no part in it

- **Status:** Accepted
- **Date:** 2026-10-13
- **Deciders:** Vadim Scheibe

## Context

The owner's people work at Windows PCs of an Active Directory domain, and Keycloak already reads
that AD over LDAP. Windows can sign a person on to a web application with no password at all: the
browser presents the Kerberos ticket the person was given at logon (SPNEGO, the `Negotiate`
scheme), and the application decrypts it with its own key. The owner asked whether AIRA can do
that, and to have it proven for both components — the console and the gateway — for the people who
must get in and for those who must not.

AIRA authenticates two ways (`FRD-101`): an OIDC bearer token from Keycloak, and an API key it
issued itself. Both planes validate a token by its signature, issuer and audience and read three
things from it: the subject, the username and the group paths (`ADR-0017`). Neither plane knows how
the person proved who they were.

## Options considered

- **SPNEGO in AIRA** — the gateway and Management accept `Authorization: Negotiate` themselves,
  with a keytab each. Every request then carries a Kerberos exchange, both planes need a key of the
  domain and its rotation, the person still has to be found in the directory and their groups read,
  and a second identity path exists beside the token — whose subject differs from the one
  `ADR-0007` binds a person to. It also cannot work where clients live: a coding assistant or a
  script holds no ticket for the gateway, and the browser cannot attach one to the console's API
  calls.
- **SPNEGO in Keycloak** — Keycloak's LDAP federation accepts the ticket in the browser flow and
  issues the same tokens a password would have. AIRA is unchanged; the person is found in the same
  directory, through the same federation, with the same groups.
- **Microsoft Entra ID brokered by Keycloak** — the same outcome for a tenant synchronised with
  Entra, with Entra's own seamless sign-on in front. A different project for an installation that
  has Entra; for this one it adds a tenant without removing the domain.

## Decision

**Windows sign-on is configured in the domain, in Keycloak and on the PCs, and nowhere in AIRA.**

- The domain gives Keycloak a service principal `HTTP/<the host of AIRA_OIDC_ISSUER>`, AES keys
  and a keytab; Keycloak's existing federation allows Kerberos; the browsers are told to trust
  Keycloak's host. A browser without a ticket, or not told to trust it, gets Keycloak's form — so
  the sign-on fails safe and nobody is locked out by it.
- **Nothing in either plane learns that a ticket was involved**, and nothing has to. A token is a
  token; the gateway's decision about a use case is the group the token carries, whichever way the
  person signed in. Non-browser clients keep API keys (`FRD-101`), which is what they use today.
- **The proof lives in this repository** rather than in a statement: a Samba domain controller
  stands in for the AD (`deploy/compose/ad`, profile `windows-sso`), `tools/windows_sso.py` makes
  both administrators' changes against it, and `make test-windows-sso` drives a browser holding a
  person's ticket through the console and their token to the gateway — for the people who get in
  and for those who must not (`FRD-140`). It runs in CI after the browser suite.
- The realm's half is **taken away again after the suite**, passed or not: every other suite runs
  against a realm of local accounts, and a federation left in it would answer their directory
  searches with people from the domain.

## Consequences

- Positive: the owner's installation needs no AIRA release for Windows sign-on — an SPN, a keytab,
  four fields on the federation and a browser policy. `docs/INTEGRATIONS.md` §2, *Windows sign-on*,
  is that list.
- Positive: the proof found what decides **when somebody is out**, which is not Kerberos at all:
  Keycloak's cache policy. With its default a person disabled in AD can renew their session and a
  person taken out of a group keeps it, on a fresh sign-on too; a bounded lifespan makes the delay
  a number. That holds for password sign-ons through the federation as well, and is stated in the
  guide for the installation that already has one.
- Negative: AIRA cannot tell an auditor how a person signed in — Keycloak's events can.
- Negative: the stand-in is Samba, not Windows. What it cannot show is Windows' own behaviour on the
  client — the browser policy, the intranet zone — which the guide states rather than proves.
- Follow-up: a client that should sign on without a browser (a CLI on a domain PC) would need the
  device or authorization-code flow in front of Keycloak, not a ticket at the gateway.
