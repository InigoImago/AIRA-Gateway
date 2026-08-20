# FRD-612 — A declaration the console accepts and the dialect cannot say

> Phase: 6 · Status: **Built** · Owner: Vadim Scheibe
>
> Origin: the owner: *"you broke my showcase — if you now change something on `qwen3:0.6b`, it
> cannot cope with thinking methods. And set the KIRA model id to 1004."*
> Related: `ADR-0021` (levels are the vendor's words), `FRD-111` (thinking control), `FRD-114`
> (a model declares what it allows), `FRD-126` (one pre-dispatch owner), `FRD-506` (inform, never
> block), `FRD-107` (the KIRA surface).

## 1. What was reported, and what it turned out to be

`make showcase` ran green, so the report was reproduced the way it was described: open the model in
the console, tick a box, save. Ticking **`auto`** for `qwen3:0.6b` — served by an OpenAI-dialect
endpoint — takes ten seconds, is offered without a word of warning, and turns every thinking
request into:

```json
{"error": {"code": 500, "message": "Internal error while processing the request."}}
```

Three defects, one shape: **the catalogue can claim something the model's wire format cannot say,
and nothing anywhere notices until a caller does.**

## 2. Decision

### 2.1 A mapping that cannot express the request is a refusal, not a crash

`DialectUnsupported` was **not in `REFUSALS`** — the single list both surfaces catch, written so
that *"a new control cannot be caught by one surface and escape the other"* (`FRD-126`). Its own
docstring explains why nobody noticed:

> *"It should be unreachable in practice — a model that cannot do a thing does not declare the
> capability."*

True of the seed. False of the screen where a Global Administrator declares one. **An exception
whose unreachability depends on nobody making an ordinary mistake in a form is reachable.**

Both surfaces now name the reason. Verified live against the request that produced the 500:

| surface | status | body |
| --- | --- | --- |
| Gemini | `400 FAILED_PRECONDITION` | *"This dialect has no way to say 'the model decides': `reasoning_effort` is always a level…"* |
| KIRA | `400 VALIDATION_ERROR` | the same sentence, in the predecessor's envelope |

`FAILED_PRECONDITION` for the same reason `NoCapableModel` uses it: this is a configuration
somebody can correct, not an outage. On the compatibility surface it is `VALIDATION_ERROR` and
deliberately **not** `MODEL_NOT_FOUND` — the model exists and is reachable, and telling a client
the model is missing sends it looking for a different id.

The audit row records `invalid_request`. A 500 was recorded as the gateway failing, so a report
read a week later showed an outage where there had been a wrong declaration.

### 2.2 The declaration that nothing read

Every adapter has carried `Upstream.thinking_modes` since the dialects were split — the OpenAI
family excludes `limited` and `auto`, Anthropic excludes `auto`. `ADR-0021` §5 cites it as an
existing fact. **No code on any path read it**: four adapters declaring, one test asserting,
nothing asking. Two correct halves and no wire (`LESSONS.md` §1), in its sixth costume.

The console's *Ask the model* button now asks about the **ticked modes** as well as the level
words, and answers them from the dialect **for nothing** — whether a wire format has a field for
*"you decide"* is a fact about the dialect, not about the model or the region it runs in, so no
request leaves the gateway for it. The verdict arrives before the save:

> ✗ this dialect cannot say it — *"This model's wire format has no way to say 'auto'. Declaring it
> here means every request that asks for it is refused — the model is never reached."*

The sentence says what declaring it would **do**, because the reader is deciding whether to leave a
box ticked; *"unsupported"* alone does not tell them.

**It informs and never blocks** (`FRD-506`). The runtime refusal in §2.1 is the backstop, and a
console that refuses a save is a console that is sometimes wrong about a model it cannot reach. An
adapter that declares no modes at all is treated as able to express everything, for the same
reason: a red mark there would be this console's opinion rather than the dialect's.

### 2.3 The demo carries the predecessor's own id

`ADR-0010`, `docs/MIGRATION-KIRA.md` and the hermetic suite have used `1004` — the predecessor's
chat id — as their example since the surface was built, while the demo's chat model answered to
`9001`. So the one runnable command in a migration guide used a different number from every
sentence around it, and `FRD-107`'s promise is precisely that a client migrates by changing a base
URL. The demo is the first place that should look like an installation that kept its clients' ids.

Six integration tests carried the literal `9001`; they now import `LOCAL_CHAT_MODEL_ID` from the
seed that writes it, so the next move of the id cannot leave them addressing a model that no longer
answers. The two seeds are held equal by `tools/tests/test_local_model_measurements_agree.py`,
which already compared `numeric_id`.

### 2.4 The showcase prints a command for a model it owns

`showcase_try_it.py` exists for one sentence in its own docstring — *"what was missing was one
command that works"* — and it took whichever chat model the KIRA catalog listed **first**. On a
machine with cloud credentials that is a cloud model: it printed `model_id: 9504` for
`gemini-2.5-flash`, which the demo never seeds, never releases and never calls. Running it returned
`200` with an **empty** body — 24 output tokens, every one spent thinking.

It now prefers the demo's own chat model, read from the same variable the seed reads, and falls
back to the first chat model when there is no local one (a failed pull, or somebody pointing the
showcase at an installation of their own).

## 3. Testing

| | |
| --- | --- |
| gateway | four on the refusal — it is in `REFUSALS`, both surfaces name the reason, and the audit row says a request was refused rather than that the gateway broke |
| gateway | five on the check endpoint, including *the modes are answered from the dialect, never by spending a token on the model* — the stand-in raises if a request is made |
| console | four, two of them on the rendered DOM: the verdict sits beside the box it is about, the sentence appears only for a refusal, and nothing is marked before the button is pressed |
| tools | four on the showcase's selection, with the network stubbed |
| mutations | 7 new (557 total) |

Two of those came out of the round rather than into it. One mutation **survived** and was deleted
rather than defended — an early return that both following branches already produced; a rule
written twice is one that can be corrected in one place. And one console property was broken by
hand and **did not go red**: the test never set a verdict before failing the next question, so
there was nothing stale to leave behind. It sets one now.

## 4. Risks

- **The console's verdict is a moment in time.** It is fetched when the button is pressed, and a
  model saved afterwards carries whatever was ticked. That is the `FRD-506` trade deliberately, and
  §2.1 is why it is safe: the runtime answer names the reason either way.
- **A dialect that gains a field** — a vendor adding *"you decide"* to `reasoning_effort` — needs
  the adapter's `thinking_modes` updated, or the console will keep marking a mode that now works.
  The declaration is now read in one place, which is the smallest surface that has ever had.
