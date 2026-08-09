"""Which operator-supplied regexes are safe to run on the request path.

Nested quantifiers — `(a+)+`, `(a*)*`, `(ab|a)+` — are the classic trigger for catastrophic
backtracking: one of them against a long prompt takes exponential time and stalls a gateway worker
for as long as it runs. Python's `re` has no timeout, so the only defence is not compiling them.

**Both planes ask, because only one of them used to.** Management refused such a pattern at
authoring time and the gateway compiled whatever reached its read-model — over Kafka, from a seed,
from a direct database write, or from an older Management that predates the check. The protection
sat at one end of a link and the other end trusted it, which is the shape of three of the four
findings in `ADR-0018`. The check is cheap and the consequence of missing it is a hung worker, so
it is asked twice on purpose.

The detection is a **heuristic and says so**: it recognises the shapes that cause the problem in
practice, not every regex that could backtrack. A precise answer would need to model the engine.
Erring towards refusal is safe here because a rejected pattern can always be written another way,
and an operator hears about it at the moment they write it.
"""

from __future__ import annotations

import re

#: A quantified group containing a quantifier or an alternation — the shapes that blow up.
_NESTED_QUANTIFIER = re.compile(r"\([^)]*[+*}][^)]*\)\s*[+*]|\([^)]*\|[^)]*\)\s*[+*]")


def is_catastrophic(pattern: str) -> bool:
    """True if ``pattern`` nests quantifiers and must not be compiled for use on a request.

    A pattern that is not valid regex at all answers **False**: the gateway matches those
    literally (`_compile` falls back to `re.escape`), so they cannot backtrack and refusing them
    would reject a plain string somebody wrote with a stray bracket.
    """
    try:
        re.compile(pattern)
    except re.error:
        return False
    return bool(_NESTED_QUANTIFIER.search(pattern))
