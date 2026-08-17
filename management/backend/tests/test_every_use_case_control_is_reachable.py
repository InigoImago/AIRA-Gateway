"""Every field that steers a use case, and every pipeline setting the gateway obeys, must be
settable from the console.

The model catalog got this guard first, after `numeric_id` shipped displayed and unsettable. Asked
to do the same for use cases and pipelines, and the same shape was waiting: `description` and
`processing_notes` were **printed on the overview** — *"No description."* was what most
installations ever saw — accepted by the API, carried to the gateway's read-model, and offered by
no screen at all.

Two directions, as before. What Management will *accept* must be enterable, and what the gateway
*obeys* must be enterable — the second is the one that matters, because a control the data plane
reads and nobody can write is a decision taken by a value with no author.
"""

from __future__ import annotations

import re
from pathlib import Path

from aira_management.apps.anomalies.serializers import AnomalyRuleSerializer
from aira_management.apps.apikeys.serializers import IssueApiKeySerializer
from aira_management.apps.budgets.serializers import BudgetSerializer
from aira_management.apps.ratelimits.serializers import RateLimitSerializer
from aira_management.apps.usecases.serializers import UseCaseSerializer

REPO = Path(__file__).resolve().parents[3]
USE_CASE_PANELS = REPO / "management/frontend/src/app/features/use-cases"
PIPELINE_EDITOR = REPO / "management/frontend/src/app/features/pipelines/pipeline-editor.html"
PIPELINE_ENGINE = REPO / "gateway/src/aira_gateway/pipeline/engine.py"
RULE_FORM = REPO / "management/frontend/src/app/features/security/rule-form.ts"
USE_CASE_SERVICE = REPO / "management/frontend/src/app/core/api/use-case.service.ts"
SECURITY_PAGE = REPO / "management/frontend/src/app/features/security/security-page.ts"
INCIDENTS_API = REPO / "gateway/src/aira_gateway/api/incidents.py"
SMOKE_TESTS = REPO / "management/frontend/src/app/features/smoketests/smoke-tests.ts"


def _arguments(source: str, call: str) -> list[str]:
    """The text of each ``call(...)`` in ``source``, parentheses balanced.

    A regex to the first `)` would stop inside `this.slug()`, and a regex to the last would swallow
    the file. The console writes a use case from four different panels, so this has to be right in
    all four rather than in the one that was looked at.
    """
    found: list[str] = []
    for match in re.finditer(re.escape(call) + r"\(", source):
        index, depth = match.end(), 1
        while index < len(source) and depth:
            depth += (source[index] == "(") - (source[index] == ")")
            index += 1
        found.append(source[match.end() : index - 1])
    return found


def _fields_the_console_writes() -> set[str]:
    """Every use-case field any panel sends, in both object spellings."""
    written: set[str] = set()
    for panel in sorted(USE_CASE_PANELS.glob("*.ts")):
        if panel.name.endswith(".spec.ts"):
            continue
        source = panel.read_text(encoding="utf-8")
        for call in ("service.update", "service.create"):
            for body in _arguments(source, call):
                written.update(re.findall(r"([a-z_]+)\s*:", body))
                # `{ slug, name: … }` — shorthand is the same field, and the model guard learned
                # this the same way: a spelling the guard does not know reads as a missing control.
                for literal in re.findall(r"\{([^{}]*)\}", body):
                    written.update(re.findall(r"(?:^|,)\s*([a-z_]+)\s*(?=[,}]|$)", literal))
    return written


#: Writable fields no panel sends, with the reason.
#:
#: `slug` is the use case's **key**: it is chosen once, at creation (`use-case-list.ts` derives it
#: from the name), and appears in every API key, every audit row and every `/uc/<slug>` URL a
#: client is configured with. An editable primary key is not a missing control, it is a different
#: feature — renaming a use case would have to rewrite what callers are pointed at.
NOT_A_CONTROL = {
    "slug": "The key, set once at creation; changing it would invalidate every client's URL.",
}


def test_every_writable_use_case_field_leaves_the_console() -> None:
    writable = {name for name, field in UseCaseSerializer().fields.items() if not field.read_only}
    unreachable = sorted(writable - _fields_the_console_writes() - set(NOT_A_CONTROL))

    assert not unreachable, (
        f"These use-case fields are writable through the API and no panel sends them: "
        f"{unreachable}. `description` and `processing_notes` sat here — printed on the overview, "
        "settable nowhere, so every installation's overview said 'No description.' forever."
    )


def test_the_exemptions_are_still_writable_fields() -> None:
    writable = {name for name, field in UseCaseSerializer().fields.items() if not field.read_only}
    stale = sorted(set(NOT_A_CONTROL) - writable)

    assert not stale, f"NOT_A_CONTROL names fields the serializer no longer writes: {stale}."


#: Step-config keys the gateway reads but no operator authors, with the reason.
#:
#: Empty, and it should stay that way: every one of the twelve keys `engine.py` consults is offered
#: by the builder. An entry here means the data plane obeys something nobody can type.
NOT_AUTHORED: dict[str, str] = {}


def test_every_pipeline_setting_the_gateway_obeys_can_be_authored() -> None:
    """What `engine.py` reads out of a step's config, against what the builder can set.

    This is the pipeline's version of the `ModelDeclaration` check, and it is the one worth having:
    a step's config is free-form JSON on both sides, so a key the engine starts reading is a
    control that silently exists — and its **default** is what every use case gets until somebody
    notices. `action` defaulting to `"block"` and `use_builtins` to `True` are the difference
    between a filter that stops a request and one that watches it go past.
    """
    engine = PIPELINE_ENGINE.read_text(encoding="utf-8")
    read_by_the_engine = set(re.findall(r'config\.get\(\s*"([a-z_]+)"', engine))
    editor = PIPELINE_EDITOR.read_text(encoding="utf-8")
    settable = set(re.findall(r"set(?:Step|List)Field\([^,]+,\s*'([a-z_]+)'", editor, re.S))
    # `categories` is authored entry by entry rather than as one field.
    settable |= {"categories"} if "setCategoryField(" in editor else set()

    unauthored = sorted(read_by_the_engine - settable - set(NOT_AUTHORED))

    assert not unauthored, (
        f"The pipeline engine obeys these and the builder cannot set them: {unauthored}. Whatever "
        "the engine's default is becomes the behaviour of every use case, chosen by nobody."
    )


def test_every_field_of_a_routing_category_can_be_authored() -> None:
    """A category is three strings, and the description is the load-bearing one.

    `classifiers.py` builds the router's prompt as ``- {name}: {description}``, so the description
    is *how the classifier is told what the category means*. A category list somebody could name
    and not describe would route by nothing and look configured.
    """
    editor = PIPELINE_EDITOR.read_text(encoding="utf-8")
    settable = set(re.findall(r"setCategoryField\([^,]+,\s*\$index,\s*'([a-z_]+)'", editor, re.S))

    assert {"name", "description", "model"} <= settable, sorted(settable)


def _typed_literals(kind: str) -> set[str]:
    """The keys of every object literal annotated ``: <kind> =`` in the use-case panels.

    Budgets and rate limits are not sent field by field: the tab builds one typed object and hands
    it over, so the payload is the literal rather than the call. Reading the *annotation* is what
    keeps this honest — an untyped `{...}` would let a field quietly disappear from the payload
    while the guard kept passing.

    **Scoped to the file that owns the payload.** The spread form `{ ...limit, enabled }` carries
    every field of an existing row plus the one being changed, and counting spreads from every
    panel at once let the rate-limit tab's switch answer for the budget tab's — the guard passed
    with `enabled` removed from budgets entirely. A guard that can be satisfied by a *different*
    screen is not a guard.
    """
    keys: set[str] = set()
    for panel in sorted(USE_CASE_PANELS.glob("*.ts")):
        if panel.name.endswith(".spec.ts"):
            continue
        source = panel.read_text(encoding="utf-8")
        literals = re.findall(rf":\s*{kind}\s*=\s*\{{([^}}]*)\}}", source)
        if not literals:
            continue
        for literal in literals:
            keys.update(re.findall(r"([a-z_]+)\s*:", literal))
        keys.update(re.findall(r"\{\s*\.\.\.[a-zA-Z]+,\s*([a-z_]+)\s*\}", source))
    return keys


#: Fields of a budget or a rate limit that no panel sends, with the reason.
#:
#: `subject` is on the wire and ignored: no scope names a person any more (2026-08-14), and the
#: field is kept while both planes' migrations run. It is sent as `''` by both tabs, so it is
#: reachable in the only sense that matters — but naming it here would be wrong, and it does not
#: need to be: the tabs do send it.
LIMIT_FIELDS_NOT_SENT: dict[str, str] = {}


def test_every_writable_budget_and_rate_limit_field_leaves_the_console() -> None:
    """Both, in one test, because they are the same screenful of the same decision.

    `enabled` sat here in **both**. The gateway obeys it — it selects only enabled budgets, and
    skips a rate limit whose flag is off — the rate-limit table even printed an *Active/Disabled*
    badge whose other half no control could reach, and the budget card did not mention it at all.
    Worse than unreachable: the endpoint upserts, so every console save re-armed a limit somebody
    had deliberately lifted, silently.
    """
    for kind, serializer in (("Budget", BudgetSerializer), ("RateLimit", RateLimitSerializer)):
        writable = {name for name, field in serializer().fields.items() if not field.read_only}
        unreachable = sorted(writable - _typed_literals(kind) - set(LIMIT_FIELDS_NOT_SENT))

        assert not unreachable, (
            f"These {kind} fields are writable through the API and no panel sends them: "
            f"{unreachable}. The endpoint upserts, so a field the console never mentions is not "
            "merely unreachable — it is reset to its default on every save."
        )


def test_every_writable_anomaly_rule_field_leaves_the_console() -> None:
    """One form authors both a use case's rules and the global ones (`rules-tab` imports it).

    Every field the **evaluator** reads — kind, window, threshold, parameter, sample floor, action,
    target, action minutes, throttle — decides whether somebody's traffic is suspended, and
    `enabled` decides whether the rule is consulted at all (`AnomalyRuleRead.enabled.is_(True)`).
    A rule half-authored from a form is a control whose remaining half is a default nobody chose.

    **Read from `submit()`, not from any `: AnomalyRule =` literal in the file.** The first version
    matched both, and this file's other one is `NEW_RULE` — the *template* a blank form starts
    from, which naturally names every field. So the guard passed with `min_sample` deleted from the
    payload: a default answered for a control. That is twice today that a guard was satisfied by
    the wrong source, and both times the tell was the same — it kept passing under a mutation it
    was written to catch. **Point a reachability check at the thing that is sent.**
    """
    source = RULE_FORM.read_text(encoding="utf-8")
    start = source.index("  protected submit(): void {")
    submit = source[start : source.index("\n  }", start)]
    sent = set(re.findall(r"([a-z_]+)\s*:", submit))

    writable = {n for n, f in AnomalyRuleSerializer().fields.items() if not f.read_only}
    unreachable = sorted(writable - sent)

    assert not unreachable, (
        f"These anomaly-rule fields are writable and the form does not send them: {unreachable}."
    )


def test_every_field_of_an_issued_api_key_leaves_the_console() -> None:
    """Issuing is its own contract: `IssueApiKeySerializer`, not the read serializer.

    Three fields — the label, who owns it, and how long it lives — and each is a governance answer
    rather than a convenience. `expires_in_days` in particular: the serializer fills in the
    configured default and refuses anything past the configured maximum, so a console that could
    not send it would make every key live exactly as long as the installation's default and give
    nobody a way to ask for less.
    """
    source = USE_CASE_SERVICE.read_text(encoding="utf-8")
    start = source.index("  issueApiKey(")
    body = source[start : source.index("\n  }", start)]
    sent = set(re.findall(r"([a-z_]+)\s*:", body)) | set(re.findall(r"body\.([a-z_]+)", body))

    writable = {n for n, f in IssueApiKeySerializer().fields.items() if not f.read_only}
    unreachable = sorted(writable - sent)

    assert not unreachable, (
        f"These fields of an issued API key are writable and the console does not send them: "
        f"{unreachable}."
    )


def test_every_field_of_the_kill_switch_can_be_set_by_a_person() -> None:
    """`POST /v1beta/suspensions` is the incident control, and it lives in the gateway.

    Read from `create_suspension`'s own `body.get(...)` calls rather than from a serializer, because
    that endpoint deliberately bypasses Management and Kafka (`FRD-503` §4.3) — a control that
    depends on the event bus fails when the bus is the problem.

    Three of its fields — `use_case`, `action`, `throttle_rpm` — were *rendered* by the suspensions
    table and produced by no form, because a **rule** can create a scoped or throttled suspension
    and a person could not. Every manual decision was therefore a full block, everywhere.
    """
    api = INCIDENTS_API.read_text(encoding="utf-8")
    start = api.index("async def create_suspension(")
    handler = api[start : api.index("\n@router", start)]
    accepted = set(re.findall(r'body\.get\(\s*"([a-z_]+)"', handler))

    page = SECURITY_PAGE.read_text(encoding="utf-8")
    start = page.index("  protected stop(): void {")
    sent = set(re.findall(r"([a-z_]+)\s*:", page[start : page.index("\n  }", start)]))

    unreachable = sorted(accepted - sent)

    assert accepted, "no `body.get` in create_suspension — the extractor, not the endpoint"
    assert not unreachable, (
        f"The kill switch accepts these and no person can set them: {unreachable}. Whatever the "
        "endpoint defaults to is then the only decision a human can make during an incident."
    )


def test_a_question_leaves_the_catalogue_by_being_retired() -> None:
    """`retired` is the one field of a question that is not part of its wording.

    It exists because a verdict was formed against that wording — `TestResult.case` is `PROTECT`,
    and the console's *Remove* therefore raised an unhandled `ProtectedError`, a 500, for every
    question the catalogue had ever been run against. The field had no caller anywhere.
    """
    console = SMOKE_TESTS.read_text(encoding="utf-8")

    assert "retired: true" in console, (
        "nothing in the smoke-test console retires a question. Deleting one that has been answered "
        "is refused by the server (PROTECT), so without this the catalogue cannot be corrected."
    )
