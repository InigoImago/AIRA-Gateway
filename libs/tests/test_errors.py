import pytest

from aira_common.errors import (
    AiraError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)


def test_base_defaults() -> None:
    err = AiraError("boom")
    assert err.status_code == 500
    assert err.code == "internal_error"
    assert err.message == "boom"
    assert err.details is None


def test_overrides() -> None:
    err = AiraError("nope", code="custom", status_code=418, details={"k": "v"})
    assert err.code == "custom"
    assert err.status_code == 418
    assert err.details == {"k": "v"}


def test_to_response_shape() -> None:
    resp = AiraError("bad", code="x", details={"a": 1}).to_response()
    assert resp.error.code == "x"
    assert resp.error.message == "bad"
    assert resp.error.details == {"a": 1}


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (NotFoundError, 404, "not_found"),
        (UnauthorizedError, 401, "unauthorized"),
        (ForbiddenError, 403, "forbidden"),
    ],
)
def test_subclasses(exc: type[AiraError], status: int, code: str) -> None:
    err = exc("msg")
    assert err.status_code == status
    assert err.code == code
    assert isinstance(err, AiraError)
