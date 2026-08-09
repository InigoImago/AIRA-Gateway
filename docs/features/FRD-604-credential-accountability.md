# FRD-604 — Who answers for a credential

> Phase: 5 (IT Security) · Status: **Stage A done (2026-08-09)**, Stage B specified · Owner: Vadim Scheibe
> Last updated: 2026-08-09
> Origin: the owner, describing the two use cases this installation is being built for.
> Related: `FRD-205` (self-service keys), `FRD-122` (audit), `FRD-505` (requests view),
> `FRD-131`/`FRD-132` (tool calling), `ADR-0013` (scope).

## 1. Problem

Two use cases with opposite credential shapes are coming, and one console serves both:

- **Agentic coding.** People issue their **own** keys and hand them to a coding assistant. When one
  misbehaves, IT Security's question is *whose agent was that* — and the answer is the person the
  key belongs to. `FRD-132` measured the traffic shape: one trivial instruction produced three
  gateway requests. An agent generates volume nobody typed.
- **A RAG chatbot** with no agentic capability, served by one credential for a whole service.

The chain already existed and was never stated. `ApiKey.owner` is a foreign key to a person, the
issue event carries `subject = user.get_username()`, the gateway stores it on the key and writes it
onto **every** audit row beside the key's `credential` prefix, and the requests view can filter by
key. Nothing was missing in the data.

**What was missing is that nobody was told.** The console recorded the issuer and never said so at
the moment of issuing, and it printed that person's username beside an agent's traffic with no
indication that it names *who answers for the credential* rather than *who wrote the request*. An
investigator reads a colleague's name next to a rogue agent's requests and draws the obvious wrong
conclusion — which is a worse failure than an absent figure, because it is a confident one and it
is about a person.

## 2. Goals & Non-Goals

**Goals**
- The person issuing a key is told, **before** they issue it, that everything done with it is
  recorded under their name.
- The requests view distinguishes *the owner of a credential* from *an interactive caller*, in
  words, on the row.
- Wording that is true of a personal key **and** of a shared one. A team key is a legitimate
  arrangement; a console implying a human sat behind every request would mislead precisely the
  reader who came to find one.

**Non-Goals**
- Identifying *which human* drove an agent behind a shared key. That is not knowable from a
  credential, and inventing an answer is worse than the honest one (§5.2).
- Blocking or discouraging shared keys.
- Any change to what is recorded. The audit trail was already complete.

## 3. User Stories
- As **IT Security**, when a coding agent misbehaves, I want to reach a person from the request —
  and I want the screen to tell me whether that person is accountable for the credential or was the
  one typing.
- As a **developer** issuing a key for my assistant, I want to know that its traffic carries my
  name before I click, not after an incident.

## 4. Functional Requirements

- **FR-1** The issue form states, before the button, that requests made with the key are recorded
  under the issuer's name and that they answer for its use — *including by anything they hand it to*.
- **FR-2** The same fact is repeated beside the plaintext key, which is the last moment anybody
  reads that panel.
- **FR-3** The `Owner` column explains what an owner is: accountable for the credential, not the
  author of the request.
- **FR-4** A request made with an API key is **marked as such** on the row. An OIDC caller is not
  marked — there the name *is* the person, and marking both makes the distinction useless.
- **FR-5 (Stage B, not built)** A key may be **owned by** one identity and **issued by** another,
  so a team or service credential names a technical account while the console still records which
  human created it.

## 5. Design & Architecture

### 5.1 The words are the mechanism

Nothing is computed here. Stage A is four sentences and a badge, and it is a feature because the
defect it fixes is a wrong conclusion a reader draws — the same class as `FRD-206`'s buttons that
403 and `FRD-505`'s 200 rendered in red.

The marker carries its meaning **in its text**, not in a `title`. `FRD-206` paid for that twice: a
tooltip shows nothing on a touch screen, needs a long hover with a mouse, and is invisible to a
keyboard.

### 5.2 What a credential can and cannot tell you

An API key answers *which system*, and through its owner *who is accountable*. It cannot answer
*who typed this*, and for a shared key nothing can. `FRD-122` already stated the first half — "the
API-key prefix identifies the calling system" — and the console simply had not carried it through
to the screen where somebody acts on it.

That is why FR-4 is a marker rather than a replacement: removing the name would lose the
accountability chain, and leaving it bare implies authorship. Both facts are on the row.

### 5.3 Stage B: owner and issuer are different questions

For the chatbot case the owner should be a technical account, and there are two ways to get one.

**Signing in as the technical user** works today with no code and is wrong: it needs shared
credentials for a *governance* console, and it destroys the fact that matters — which human created
the credential. The console would record "svc-kundenservice issued a key" and nobody knows who that
was.

**Issuing on behalf of** keeps both: `owner` is the technical account (whose name every audit row
carries, correctly — a row describes what called, not who authorised the credential months ago) and
`issued_by` is the human. The same shape as `UseCaseGroupGrant.granted_by` and a suspension's
author, both of which this project already decided.

The owner is picked from the **directory**, not typed, so it names a real identity in the identity
provider. The picker exists inside `access-panel` and would be extracted — the move `info-hint`
already made after being written twice.

## 6. Data Model

Stage A: none.

Stage B: `ApiKey.issued_by` beside `owner`, a migration, the field on `api_key.created`, and the
column on the gateway's read model.

## 7. API / Interface Contract

Stage A is console-only. No endpoint changed, no event changed, no audit column added.

## 8. Security & Privacy

- The marker exposes nothing new: `credential` is already on the row and is the public half of the
  credential (`FRD-122`).
- Naming an owner beside traffic is the point of the screen and is already scoped by
  `visible_scope` plus the payload rules of `ADR-0016`.

## 10. Testing & Acceptance Criteria

- **Unit** — the notice is rendered before issuing and beside the plaintext; a row with a
  credential is marked; a row **without** one is not. Each shown to fail first, and the last of
  them needed the **inverse** mutation (marking everything), because a test asserting an absence
  cannot go red when the code that produces it is deleted — `N50`'s lesson.
- **e2e** — the notice is present in a real browser at the moment a key is issued.

**Acceptance**
- *Given* an agent's request made with a key, *when* IT Security opens the requests view, *then*
  the row names the person who answers for the credential **and** says the request was made with a
  key rather than by that person interactively.

## 11. Dependencies & Risks

- **Risk — the wording is read as blame.** It states responsibility for a credential, which is what
  issuing one is. It deliberately does not say the owner wrote anything.
- **Risk — Stage A is mistaken for Stage B.** A team that reads FR-1 and issues a shared key under
  one person's name has done nothing wrong; FR-5 is what makes the better arrangement available.
