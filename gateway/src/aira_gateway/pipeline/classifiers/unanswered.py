"""What a call to a step's model can end in without an answer (`FRD-302` FR-8).

One definition for the three classifiers, because what they catch decides where a failure goes. A
model that could not be reached, addressed or sent the request has not answered, so the step's
chain moves on to its next model and, once it is exhausted, the step takes its own safe direction —
the filter decides on *undetermined*, the router takes its default, the redactor blocks unless told
otherwise. A failure a classifier does not catch goes past all three, and past the decision row
that would have said why.
"""

from __future__ import annotations

from aira_gateway.residency import RegionNotAllowed
from aira_gateway.upstreams.base import AmbiguousModel, DialectUnsupported, UpstreamError

#: The failures a dispatch chain passes a candidate over for: reached and failed (`UpstreamError`),
#: nowhere this gateway may send it — no region catalogued, or one residency forbids
#: (`AmbiguousModel`, `RegionNotAllowed`) — or asked for something its wire format cannot carry
#: (`DialectUnsupported`). Anything else is a fault in this gateway and propagates.
UNANSWERED: tuple[type[Exception], ...] = (
    UpstreamError,
    AmbiguousModel,
    RegionNotAllowed,
    DialectUnsupported,
)


def why_unanswered(error: Exception) -> str:
    """Why the call got no answer, in the words of whoever stopped it."""
    return error.message if isinstance(error, UpstreamError) else str(error)
