"""Signing in again must stop when signing in is not the problem.

**The defect, reported from use.** You can authenticate against Keycloak perfectly well, and the
installation refuses the token it issues — a mismatched audience or issuer, a clock too far apart,
a session this deployment no longer recognises. The console's interceptor treats every `401` on a
first-party call as *this session is over* and redirects to the login. Keycloak still holds a valid
SSO session, so it answers the authorization request without asking anybody anything and redirects
straight back. The console exchanges the code, calls the API, is refused again, and goes round —
flickering through the round trip as fast as the browser can navigate, throwing an error each time,
until Keycloak's brute-force limit locks the account out.

**Why the guard that existed could not see it.** `AuthService.reauthenticate` had a
`reauthenticating` flag so that five panels each getting a `401` would not start five logins. That
is a real guard for a real case, and it is *in the object* — while the thing it would have to
survive is a full-page navigation to Keycloak, which destroys the object. Its own comment said so:
*"the flag is never cleared, because the only thing that follows is a full-page navigation"*. The
guard was correct about the case it was written for and structurally blind to the one that hurts.
`LESSONS.md` §1's shape, in a new costume.

**Why this test is in Python.** The behaviour is guarded properly by Vitest, in
`auth.service.spec.ts`, which drives the loop across four freshly built services — the closest a
test gets to four page loads. But `tools/mutation_check.py` runs **pytest** and nothing else, so a
mutation that reintroduced the defect would be reported as *caught* by no test the harness can run.
The frontend mutations this repository already has (`C12`–`C14`) are guarded the same way: a Python
test that reads the TypeScript. What is asserted here is therefore the *shape* of the fix, and the
behaviour is asserted next door.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SERVICE = ROOT / "management" / "frontend" / "src" / "app" / "core" / "auth" / "auth.service.ts"
INTERCEPTOR = (
    ROOT / "management" / "frontend" / "src" / "app" / "core" / "auth" / "auth.interceptor.ts"
)
SHELL = ROOT / "management" / "frontend" / "src" / "app" / "app.html"


@pytest.fixture(scope="module")
def service() -> str:
    return SERVICE.read_text(encoding="utf-8")


def test_the_attempts_are_counted_outside_the_service(service: str) -> None:
    """**In `sessionStorage`, because a field cannot survive what is being counted.**

    What is counted is a redirect to Keycloak and back — a full-page navigation, which destroys
    every field this application has. A counter held in the service is `0` again on the way back,
    which is exactly how the loop stayed invisible to the guard that was already there.
    """
    assert "sessionStorage" in service, (
        "the login counter has to outlive a full-page navigation, so it cannot be a field"
    )
    assert "aira.reauth-attempts" in service


def test_there_is_a_limit_and_a_window(service: str) -> None:
    """A count with no window would eventually stop a console that is working: three ordinary
    expiries over a long afternoon are not a loop."""
    assert "LOOP_LIMIT" in service and "LOOP_WINDOW_MS" in service


def test_the_limit_is_consulted_before_the_redirect(service: str) -> None:
    """The order is the whole property. Counting after `initCodeFlow` would count nothing: the
    navigation has already happened, and the page that would read the counter is gone."""
    body = service[service.index("reauthenticate(): void {") :]
    body = body[: body.index("\n  }")]

    assert body.index("LOOP_LIMIT") < body.index("initCodeFlow"), (
        "the attempt count must be checked before the redirect, or the redirect always wins"
    )


def test_the_way_out_ends_the_session_at_the_provider(service: str) -> None:
    """**`logOut()`, not `logOut(true)`.**

    The local-only form clears the tokens here and leaves the Keycloak SSO session standing — so
    the next navigation is signed straight back in, and that is the loop with an extra step.
    Keycloak is the half that keeps saying yes, and the escape has to reach it.
    """
    escape = service[service.index("signOutCompletely(): void {") :]
    escape = escape[: escape.index("\n  }")]

    assert "this.oauth.logOut()" in escape, (
        "a local-only logout leaves the SSO session that causes the loop"
    )
    assert "logOut(true)" not in escape


def test_a_call_that_answers_clears_the_count() -> None:
    """The only honest evidence that the loop is over.

    Everything the console can check about *itself* — a token that parses, an expiry in the future
    — was just as true on every pass through the loop. A first-party response was not.
    """
    interceptor = INTERCEPTOR.read_text(encoding="utf-8")

    assert "noteFirstPartySuccess" in interceptor
    assert "HttpResponse" in interceptor, "a success has to be told apart from an error to count"


def test_the_reader_is_told_and_given_the_way_out() -> None:
    """A console that simply stopped redirecting would look like the one that was flickering, minus
    the explanation — and the reader would still have no way to leave."""
    shell = SHELL.read_text(encoding="utf-8")

    assert 'data-testid="login-loop"' in shell
    assert "signOutCompletely()" in shell
