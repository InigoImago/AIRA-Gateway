"""The AIRA error envelope (`aira_common.errors`), as the gateway's handlers serialise it."""

from aira_common.errors import ErrorDetail, ErrorResponse


def test_the_envelope_carries_a_code_and_a_message() -> None:
    envelope = ErrorResponse(error=ErrorDetail(code="not_found", message="No such route."))

    assert envelope.model_dump(exclude_none=True) == {
        "error": {"code": "not_found", "message": "No such route."}
    }


def test_details_travel_when_there_are_some() -> None:
    envelope = ErrorResponse(error=ErrorDetail(code="x", message="m", details={"field": "name"}))

    assert envelope.model_dump(exclude_none=True)["error"]["details"] == {"field": "name"}
