from aira_gateway.persistence.redaction import NoOpRedactor, Redactor


def test_noop_redactor_passes_through() -> None:
    redactor = NoOpRedactor()
    payload = {"prompt": "secret-ish"}
    assert redactor.redact(payload) == {"prompt": "secret-ish"}
    assert isinstance(redactor, Redactor)
