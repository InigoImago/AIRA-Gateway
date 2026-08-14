"""Telling the caller that their own request was changed under them (`FRD-309`).

**The first time AIRA edits a model's answer**, which is why the rules about where it may not go
matter more than the case where it does.
"""

from __future__ import annotations

from aira_gateway.api.serving import notice_outcome, with_notices
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage, ToolCallPart

NOTICE = "Hinweis: Die Eingabe wurde vor der Verarbeitung angepasst."


def _answer(text: str = "Die Rechnung geht raus.", **over: object) -> CanonicalResponse:
    return CanonicalResponse(
        model="m", text=text, usage=CanonicalUsage(prompt_tokens=1, completion_tokens=1), **over
    )


def test_a_text_answer_carries_the_notice_in_front_of_it() -> None:
    result = with_notices(_answer(), (NOTICE,))

    assert result.text.startswith(NOTICE)
    assert result.text.endswith("Die Rechnung geht raus.")


def test_nothing_is_changed_when_there_is_no_notice() -> None:
    answer = _answer()

    assert with_notices(answer, ()).text == answer.text


def test_a_tool_call_is_left_alone() -> None:
    """The answer *is* the call. A sentence in front of it is text a client parsing a function
    call has nowhere to put, and prepending one would turn a working agent turn into a broken
    one."""
    answer = _answer(text="", tool_calls=(ToolCallPart(id="c1", name="read_file", arguments={}),))

    assert with_notices(answer, (NOTICE,)).tool_calls == answer.tool_calls
    assert with_notices(answer, (NOTICE,)).text == ""


def test_an_answer_with_no_text_is_left_alone() -> None:
    assert with_notices(_answer(text="   "), (NOTICE,)).text == "   "


def test_a_withheld_notice_is_recorded_rather_than_skipped_silently() -> None:
    """**"No notice shown" and "nothing was redacted" are different facts**, and a reader has no
    way to tell them apart from an answer alone. So the row says which happened.

    This is the case a structured-output client hits: the document is left intact — a sentence in
    front of it would make it unparseable, which is a worse outcome than not being told — and the
    fact that the caller was therefore not told is on the trail.
    """
    shown = notice_outcome(_answer(), (NOTICE,))
    withheld = notice_outcome(
        _answer(text="", tool_calls=(ToolCallPart(id="c1", name="f", arguments={}),)), (NOTICE,)
    )

    assert shown == {"step": "notice", "action": "shown", "why": ""}
    assert withheld is not None and withheld["action"] == "withheld"
    assert "no plain text" in withheld["why"]


def test_nothing_is_recorded_where_nothing_was_owed() -> None:
    assert notice_outcome(_answer(), ()) is None
