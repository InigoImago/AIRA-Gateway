"""What a mail says (`FRD-409` FR-5, FR-6, FR-10), in English, as plain text.

A mail is read where the console's rules do not reach: forwarded, archived, open on a shared
screen. So a budget mail carries only figures its reader may already see — their own, or the use
case's to its administrators — and an anomaly mail names no person, key or address at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from email.message import EmailMessage
from email.utils import format_datetime, make_msgid

from aira_common.anomalies import RuleKind, RuleTarget, threshold_unit
from aira_common.money import format_display
from aira_gateway.anomalies.evaluator import without_addresses
from aira_gateway.db.models import AnomalyEvent
from aira_gateway.mail.recipients import Recipient
from aira_gateway.scopes import MEMBER, USE_CASE

#: How each period reads in a sentence, and how the figures so far are introduced.
_PERIOD_WORDS = {"day": "daily", "month": "monthly"}
_SO_FAR = {"day": "Today so far", "month": "This month so far"}

#: How each capped dimension is named beside its figures.
_DIMENSIONS = {"cost": "Cost", "requests": "Requests", "tokens": "Tokens"}

#: What a finding concerned, said without saying who (`FRD-409` FR-6).
_CONCERNED = {
    RuleTarget.SUBJECT.value: "one person",
    RuleTarget.CREDENTIAL.value: "one API key",
    RuleTarget.USE_CASE.value: "the use case as a whole",
}

_SIGNATURE = "-- \nAIRA Gateway\n"


@dataclass(frozen=True, slots=True)
class BudgetState:
    """A counter against the budget that binds it, as a mail reports it."""

    use_case: str
    use_case_name: str
    #: `use_case`, `each_member` or `member` (`aira_gateway.scopes`).
    scope: str
    period: str
    #: Used ÷ limit in percent, rounded down, over the capped dimension closest to its limit.
    share: int
    #: ``(dimension, used, limit)`` per capped dimension; cost in nano-units.
    figures: tuple[tuple[str, int, int], ...]
    #: When the counter is replaced by the next period's.
    resets_at: datetime


def budget_mail(
    state: BudgetState,
    *,
    to: Recipient,
    sender: str,
    thresholds: Sequence[int],
    zone: tzinfo,
    currency: str,
    enforced: bool,
    console_url: str,
) -> EmailMessage:
    """The mail about one counter: to its person, or about the shared budget to an administrator."""
    shared = state.scope == USE_CASE
    period = _PERIOD_WORDS.get(state.period, state.period)
    name = f'"{state.use_case_name or state.use_case}"'
    used_up = state.share >= 100
    if shared:
        subject = (
            f"AIRA: the shared {period} budget of {name} is used up"
            if used_up
            else f"AIRA: {name} has used {state.share}% of its shared {period} budget"
        )
        opening = (
            f"the use case {name} has used up its shared {period} budget."
            if used_up
            else f"the use case {name} has used {state.share}% of its shared {period} budget."
        )
    else:
        subject = (
            f"AIRA: your {period} budget in {name} is used up"
            if used_up
            else f"AIRA: you have used {state.share}% of your {period} budget in {name}"
        )
        opening = (
            f"your {period} budget in the use case {name} is used up."
            if used_up
            else f"you have used {state.share}% of your {period} budget in the use case {name}."
        )

    lines = ["Hello,", "", opening, ""]
    if not shared:
        lines += [
            "It is a budget set for you alone."
            if state.scope == MEMBER
            else "It is the budget each member of the use case has.",
            "",
        ]
    lines.append(f"{_SO_FAR.get(state.period, 'So far')}:")
    width = max(len(_DIMENSIONS.get(d, d)) for d, _, _ in state.figures) if state.figures else 0
    for dimension, used, limit in state.figures:
        label = _DIMENSIONS.get(dimension, dimension).ljust(width)
        used_text, limit_text = _amount(dimension, used), _amount(dimension, limit, currency)
        lines.append(f"  {label}  {used_text} of {limit_text}")
    lines += ["", f"It resets on {_moment(state.resets_at, zone)}."]
    if used_up:
        if not enforced:
            lines.append(
                "This installation does not refuse requests over a budget; the figure is for "
                "your information."
            )
        elif shared:
            lines.append("Until then, every request of the use case is refused.")
        else:
            lines.append("Until then, your requests in this use case are refused.")
    lines.append("")
    link = _tab(console_url, state.use_case, "budgets")
    lines.append(
        f"The use case's budgets: {link}"
        if link
        else "The use case's budgets are on its Budgets tab in the console."
    )
    lines.append("")
    asked = _listed(thresholds)
    if shared:
        lines.append(
            f"You receive this mail as an administrator of {name}, which mails at {asked} of a "
            "budget."
        )
    else:
        lines.append(
            f"You receive this mail because the administrators of {name} asked for one at {asked} "
            "of a budget. Questions about it go to them."
        )
    return _message(sender, to, subject, lines)


def anomaly_mail(
    findings: Sequence[AnomalyEvent],
    *,
    use_case: str,
    use_case_name: str,
    to: Recipient,
    sender: str,
    zone: tzinfo,
    console_url: str,
) -> EmailMessage:
    """One mail listing a use case's new findings, naming nobody (`FRD-409` FR-6)."""
    name = f'"{use_case_name or use_case}"'
    count = len(findings)
    subject = (
        f"AIRA: an anomaly rule found something in {name}"
        if count == 1
        else f"AIRA: anomaly rules found {count} things in {name}"
    )
    lines = ["Hello,", "", f"the anomaly rules found the following in the use case {name}:", ""]
    for finding in sorted(findings, key=lambda row: (row.created_at, row.id)):
        lines.append(f"- {_finding(finding, zone)}")
    lines += [
        "",
        "A mail does not say which person, key or address a finding concerned.",
    ]
    link = _tab(console_url, use_case, "warnings")
    lines.append(
        f"The use case's Warnings tab shows what you may see: {link}"
        if link
        else "The use case's Warnings tab in the console shows what you may see."
    )
    lines += [
        "",
        f"You receive this mail as an administrator of {name}, whose administrators asked for "
        "mail about its anomaly findings.",
    ]
    return _message(sender, to, subject, lines)


def _finding(row: AnomalyEvent, zone: tzinfo) -> str:
    try:
        unit = threshold_unit(RuleKind(row.kind))
    except ValueError:
        unit = ""
    measured = f"{row.observed} against a threshold of {row.threshold}" + (
        f" {unit}" if unit else ""
    )
    concerned = _CONCERNED.get(row.target, "something the console names")
    what = without_addresses(row.detail).strip()
    return (
        f'{_moment(row.created_at, zone)}: rule "{row.rule_name}" ({row.kind}) measured '
        f"{measured}, over {row.sample} in {row.window_minutes} minutes"
        + (f" ({what})" if what else "")
        + f". It concerned {concerned}. Done: {row.action_taken}."
    )


def _amount(dimension: str, value: int, currency: str = "") -> str:
    """An amount as a person reads it: money in the installation's currency where one is given,
    counts with thousands grouped."""
    if dimension == "cost":
        return f"{format_display(value)} {currency}".strip()
    return f"{value:,}"


def _listed(thresholds: Sequence[int]) -> str:
    """``[80, 100]`` as a sentence says it: "80% and 100%"."""
    shares = [f"{threshold}%" for threshold in thresholds]
    return shares[0] if len(shares) == 1 else f"{', '.join(shares[:-1])} and {shares[-1]}"


def _moment(moment: datetime, zone: tzinfo) -> str:
    local = moment.astimezone(zone)
    return f"{local.day} {local:%B %Y} at {local:%H:%M} ({zone})"


def _tab(console_url: str, use_case: str, tab: str) -> str:
    base = console_url.strip().rstrip("/")
    return f"{base}/use-cases/{use_case}?tab={tab}" if base else ""


def _message(sender: str, to: Recipient, subject: str, lines: list[str]) -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to.address
    message["Subject"] = subject
    message["Date"] = format_datetime(datetime.now().astimezone())
    message["Message-ID"] = make_msgid(domain="aira.invalid")
    # Not a person writing: an auto-reply to this would go nowhere useful.
    message["Auto-Submitted"] = "auto-generated"
    message.set_content("\n".join(lines) + "\n\n" + _SIGNATURE)
    return message
