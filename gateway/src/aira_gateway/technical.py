"""What a technician needs to follow one request (`FRD-630`): where it ended, when, and what the
connection and the provider said — in codes and numbers only.

Kept on the request's row (`technical`) and shown in the console beside the message the caller was
given. The sentence says what happened; this says which byte went where, at which millisecond, and
which exception or provider status word stood behind it.

**Never a sentence from outside this gateway.** A provider's message can quote the prompt, or a URL
with a key in it, and an exception's text carries whatever it was handed. So the record has a closed
set of keys, every value a number or a :data:`WORD` — a class name, a status word, a request id —
and :meth:`Technical.snapshot` drops anything else. With no person and no content in it, strict data
mode (`FRD-627`) has nothing here to withhold.
"""

from __future__ import annotations

import errno
import re
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Final

from starlette.requests import Request

#: Where the record lives on a request's state: the request-id middleware creates it as the request
#: arrives, so every millisecond below counts from then.
STATE_KEY: Final = "technical"

#: A value the record may hold as text: a class or file name, a status word, an id. Spaces are
#: allowed for `routes.py:210 in generate`, nothing that makes a sentence of it.
WORD = re.compile(r"^[A-Za-z0-9._/:@<>+=-][A-Za-z0-9._/:@<>+= -]{0,159}$")

#: The moments a request passes, in the order they normally come. Each counts milliseconds from the
#: request's arrival; a moment that never came is absent rather than zero.
MILESTONES: Final = (
    "dispatched",
    "provider_first_chunk",
    "provider_finished",
    "response_started",
    "last_byte_to_caller",
    "caller_left",
    "recorded",
)

#: Where a request ended, from its milestones (`phase_of`).
PHASES: Final = (
    "completed",
    "before_dispatch",
    "waiting_for_provider",
    "mid_answer",
    "after_provider_finished",
)

#: The response headers a provider names its own record of the request in, in the order they are
#: tried: Anthropic's, the common one, Azure's two, Google's.
REQUEST_ID_HEADERS: Final = (
    "request-id",
    "x-request-id",
    "apim-request-id",
    "x-ms-request-id",
    "x-goog-request-id",
)

#: The provider facts the record keeps, and the type each must have.
_PROVIDER_KEYS: Final[Mapping[str, type]] = {
    "status": int,
    "reason": str,
    "request_id": str,
    "retry_after": str,
    "host": str,
    "where": str,
}

#: How many links of an exception chain are kept: the outermost says what was raised, the innermost
#: what the operating system said, and a chain longer than this is the same story repeated.
MAX_CHAIN: Final = 6

#: The packages whose frames name where this gateway failed — never a library's, which says where
#: the failure surfaced, not which of our lines led there.
_OUR_PACKAGES: Final = ("aira_gateway/", "aira_common/")


class Technical:
    """One request's milestones, what reached its caller, and what it failed with."""

    def __init__(self) -> None:
        self._t0 = time.monotonic()
        self._marks: dict[str, int] = {}
        self._bytes = 0
        self._writes = 0
        self._keepalives = 0
        self._provider: dict[str, Any] = {}
        self._exception: dict[str, Any] = {}

    def _now(self) -> int:
        return round((time.monotonic() - self._t0) * 1000)

    def mark(self, milestone: str) -> None:
        """The first time ``milestone`` was reached. A later call keeps the first."""
        self._marks.setdefault(milestone, self._now())

    def reached(self, milestone: str) -> bool:
        """Whether ``milestone`` was marked."""
        return milestone in self._marks

    def wrote(self, size: int) -> None:
        """``size`` bytes of the response body went to the caller."""
        if size <= 0:
            return
        self._bytes += size
        self._writes += 1
        self._marks["last_byte_to_caller"] = self._now()

    def keepalive(self) -> None:
        """The stream was quiet, and its surface sent what its clients skip (`ADR-0046`)."""
        self._keepalives += 1

    def failed(self, exc: BaseException) -> None:
        """What the request failed with: the exception chain by class, the operating system's error
        word, the provider's facts — and, for a failure of this gateway's own, the line it came
        from. The latest failure replaces an earlier one: it is the one the caller was told."""
        chain = _chain(exc)
        found: dict[str, Any] = {"chain": [_class_name(link) for link in chain[:MAX_CHAIN]]}
        code = next((_errno_word(link) for link in chain if _errno_word(link)), None)
        if code:
            found["errno"] = code
        at = _our_line(exc.__traceback__)
        if at and getattr(exc, "facts", None) is None:
            found["at"] = at
        self._exception = found
        facts = getattr(exc, "facts", None)
        if isinstance(facts, Mapping):
            self._provider = dict(facts)
            # An error raised without facts still says the status it carries, and the cause word.
            status = getattr(exc, "status_code", None)
            if status is not None and not _fits(self._provider.get("status"), int):
                self._provider["status"] = status
            reason = getattr(getattr(exc, "diagnosis", None), "reason", None)
            if reason and not _fits(self._provider.get("reason"), str):
                self._provider["reason"] = reason

    def snapshot(self) -> dict[str, Any] | None:
        """What the row keeps, with everything outside the closed set dropped. ``None`` for a
        request that reached nothing worth a record — refused at the door, before any milestone.

        Marks nothing itself: a request may be recorded more than once — a probe, a retried write —
        and each snapshot says when it was taken."""
        if not self._marks and not self._exception:
            return None
        marks = {**self._marks, "recorded": self._now()}
        record: dict[str, Any] = {
            "phase": phase_of(marks, failed=bool(self._exception)),
            "timeline_ms": {
                milestone: marks[milestone] for milestone in MILESTONES if milestone in marks
            },
        }
        if self._writes:
            record["to_caller"] = {
                "bytes": self._bytes,
                "writes": self._writes,
                "keepalives": self._keepalives,
            }
        provider = {
            key: value
            for key, value in self._provider.items()
            if key in _PROVIDER_KEYS and _fits(value, _PROVIDER_KEYS[key])
        }
        if provider:
            record["provider"] = provider
        exception = _kept_exception(self._exception)
        if exception:
            record["exception"] = exception
        return record


def technical_of(request: Request | None) -> Technical:
    """The request's record — made here for a request that arrived past the middleware, which in
    this gateway is only a test calling a route directly, and kept by nothing for no request."""
    state = getattr(request, "state", None)
    if state is None:
        return Technical()
    found = getattr(state, STATE_KEY, None)
    if not isinstance(found, Technical):
        found = Technical()
        setattr(state, STATE_KEY, found)
    return found


def phase_of(marks: Mapping[str, int], *, failed: bool) -> str:
    """Where a request ended, from the milestones it reached.

    A caller who left while the provider was still answering left ``mid_answer``, however long the
    gateway then went on reading for the vendor's figure: that the provider finished *after* the
    caller left is what the timeline shows, not where the request ended.
    """
    left = marks.get("caller_left")
    if left is None and not failed:
        return "completed"
    if "dispatched" not in marks:
        return "before_dispatch"
    first = marks.get("provider_first_chunk", marks.get("provider_finished"))
    if first is None or (left is not None and first > left):
        return "waiting_for_provider"
    finished = marks.get("provider_finished")
    if finished is None or (left is not None and finished > left):
        return "mid_answer"
    return "after_provider_finished"


def provider_facts(
    *,
    status: int | None = None,
    reason: str | None = None,
    headers: Mapping[str, str] | None = None,
    host: str | None = None,
    where: str | None = None,
) -> dict[str, Any]:
    """The facts a provider's answer states about itself, for :class:`UpstreamError`.

    The request id is the provider's own name for this request — what its support asks for.
    """
    facts: dict[str, Any] = {}
    if status is not None:
        facts["status"] = status
    if reason:
        facts["reason"] = reason
    if headers is not None:
        request_id = next((headers[name] for name in REQUEST_ID_HEADERS if headers.get(name)), "")
        if request_id:
            facts["request_id"] = request_id
        if headers.get("retry-after"):
            facts["retry_after"] = headers["retry-after"]
    if host:
        facts["host"] = host
    if where:
        facts["where"] = where
    return facts


def _fits(value: Any, kind: type) -> bool:
    if kind is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, str) and WORD.match(value) is not None


def _kept_exception(found: Mapping[str, Any]) -> dict[str, Any]:
    kept: dict[str, Any] = {}
    chain = [link for link in found.get("chain", []) if _fits(link, str)]
    if chain:
        kept["chain"] = chain
    for key in ("errno", "at"):
        if _fits(found.get(key), str):
            kept[key] = found[key]
    return kept


def _chain(exc: BaseException) -> list[BaseException]:
    """The exception and everything it was raised from, the way a traceback reads them."""
    seen: list[BaseException] = []
    current: BaseException | None = exc
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def _class_name(exc: BaseException) -> str:
    """`httpx.ReadTimeout`, `TimeoutError`, `aira_gateway.upstreams.base.UpstreamError`: a
    library's class under its top package, which is what a search for it finds — its module path
    moves between versions — and one of ours under its whole module, which is where it is."""
    kind = type(exc)
    package = kind.__module__.split(".", 1)[0]
    if package == "builtins":
        return kind.__qualname__
    if f"{package}/" in _OUR_PACKAGES:
        return f"{kind.__module__}.{kind.__qualname__}"
    return f"{package}.{kind.__qualname__}"


def _errno_word(exc: BaseException) -> str | None:
    """`ECONNRESET` for an `OSError` that carries a number the platform names."""
    number = exc.errno if isinstance(exc, OSError) else None
    return errno.errorcode.get(number) if isinstance(number, int) else None


def _our_line(tb: TracebackType | None) -> str | None:
    """The innermost frame of this gateway's own code: `aira_gateway/api/x.py:12 in name`."""
    found: str | None = None
    while tb is not None:
        filename = tb.tb_frame.f_code.co_filename.replace("\\", "/")
        for package in _OUR_PACKAGES:
            index = filename.rfind(f"/{package}")
            if index >= 0:
                found = f"{filename[index + 1 :]}:{tb.tb_lineno} in {tb.tb_frame.f_code.co_name}"
        tb = tb.tb_next
    return found
