"""Telling the caller that their own request was changed under them (`FRD-309`).

**The first time AIRA edits a model's answer**, which is why the rules about where it may not go
matter more than the case where it does.
"""

from __future__ import annotations

from aira_gateway.api.serving import StreamedNotice, notice_outcome, with_notices
from aira_gateway.core.canonical import CanonicalResponse, CanonicalUsage, ToolCallPart

NOTICE = "Hinweis: Die Eingabe wurde vor der Verarbeitung angepasst."
DOCUMENT = '{"betrag": 42}'


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


def test_a_structured_answer_is_left_alone() -> None:
    """**The case the docstring always described and the code never checked.**

    A `responseSchema` answer is a document the caller parses. `with_notices` refused an empty
    answer and a tool call and called that "text only" — but a JSON document is neither of those:
    non-empty text, no tool call, so the sentence went in front of it and the document stopped
    parsing. The condition needs a fact about the **request**, which the function was never handed,
    which is exactly why nothing caught it (found 2026-08-15).
    """
    answer = _answer(text=DOCUMENT)

    assert with_notices(answer, (NOTICE,), structured=True).text == DOCUMENT
    assert with_notices(answer, (NOTICE,), structured=False).text.startswith(NOTICE)


def test_a_withheld_notice_is_recorded_rather_than_skipped_silently() -> None:
    """**"No notice shown" and "nothing was redacted" are different facts**, and a reader has no
    way to tell them apart from an answer alone. So the row says which happened.

    And it says **which** of the three withholdings it was: a document, a tool call, or an answer
    with no text. One message for all three teaches a reader to stop reading it — the same rule
    `payloads.PayloadRefusal` keeps for its three ways of having nothing.
    """
    shown = notice_outcome(_answer(), (NOTICE,))
    document = notice_outcome(_answer(text=DOCUMENT), (NOTICE,), structured=True)
    tool_call = notice_outcome(
        _answer(text="", tool_calls=(ToolCallPart(id="c1", name="f", arguments={}),)), (NOTICE,)
    )
    silent = notice_outcome(_answer(text="   "), (NOTICE,))

    assert shown == {"step": "notice", "action": "shown", "why": ""}
    for withheld in (document, tool_call, silent):
        assert withheld is not None and withheld["action"] == "withheld"
    assert document is not None and "parses" in document["why"]
    assert tool_call is not None and "tool call" in tool_call["why"]
    assert silent is not None and "no plain text" in silent["why"]
    # Three withholdings, three reasons — a reader can act on each of them differently.
    assert len({document["why"], tool_call["why"], silent["why"]}) == 3


def test_nothing_is_recorded_where_nothing_was_owed() -> None:
    assert notice_outcome(_answer(), ()) is None


# ---- the streamed half ---------------------------------------------------------------------


def test_a_stream_leads_with_the_notice_once() -> None:
    """A stream has no finished answer to prefix, so the notice goes in front of the first text —
    and in front of that one only, or every chunk would repeat it."""
    notice = StreamedNotice((NOTICE,))

    first = notice.lead("Die Rechnung")
    second = notice.lead(" geht raus.")

    assert first.startswith(NOTICE)
    assert first.endswith("Die Rechnung")
    assert second == " geht raus."
    assert notice.outcome() == {"step": "notice", "action": "shown", "why": ""}


def test_a_stream_that_never_produces_text_shows_nothing_and_says_so() -> None:
    """A streamed tool call has no text delta at all — the answer *is* the call. The notice is
    therefore withheld by **not happening**, which is the same condition `with_notices` tests on a
    finished answer, and the row still records it."""
    notice = StreamedNotice((NOTICE,))

    assert notice.lead("") == ""

    outcome = notice.outcome()
    assert outcome is not None and outcome["action"] == "withheld"


def test_a_structured_stream_is_never_led() -> None:
    """The caller will parse it, chunk by chunk or whole."""
    notice = StreamedNotice((NOTICE,), structured=True)

    assert notice.lead('{"betrag":') == '{"betrag":'

    outcome = notice.outcome()
    assert outcome is not None and outcome["action"] == "withheld"
    assert "parses" in outcome["why"]


def test_a_stream_owed_nothing_records_nothing() -> None:
    notice = StreamedNotice(())

    assert notice.lead("text") == "text"
    assert notice.outcome() is None
