from aira_management.config.app_settings import ManagementSettings
from aira_management.config.observability import setup_observability


def test_setup_observability_disabled() -> None:
    assert setup_observability(ManagementSettings(otel_enabled=False)) is False


def test_setup_observability_configures_the_providers() -> None:
    """Renamed from `…_instruments_django`, which is what it was called and never checked.

    It asserted a return value, and the name read as a checked fact to every later reader — while
    the instrumentation it claimed had been silently doing nothing since the flag existed
    (`FRD-615`). The middleware is now asserted below, where it can actually be seen.
    """
    settings = ManagementSettings(otel_enabled=True, otel_endpoint="http://localhost:4318")
    assert setup_observability(settings) is True


def test_instrumenting_django_puts_the_middleware_in_place(settings) -> None:
    """The property that was false for as long as the flag existed (`FRD-615`).

    `DjangoInstrumentor` instruments by **inserting a middleware**, and the call sat in
    `settings.py` above the `MIDDLEWARE = [...]` assignment — inside the import Django performs to
    build the settings object. It read a `MIDDLEWARE` that did not exist yet and the assignment
    below replaced its work, so the control plane exported no request span at all: Tempo had seen
    `aira-gateway` and never `aira-management`.

    Asserted on `settings.MIDDLEWARE` rather than on "did `instrument()` get called", because being
    called is exactly what it was doing.
    """
    from aira_management.config.observability import instrument_django
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    settings.MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
    instrumentor = DjangoInstrumentor()
    was_instrumented = instrumentor.is_instrumented_by_opentelemetry
    if was_instrumented:
        instrumentor.uninstrument()
    try:
        assert instrument_django() is True
        assert any("opentelemetry" in name for name in settings.MIDDLEWARE)
    finally:
        DjangoInstrumentor().uninstrument()
        if was_instrumented:
            DjangoInstrumentor().instrument()


def test_instrumenting_twice_adds_one_middleware(settings) -> None:
    """`ready()` can run more than once under some runners, and two middlewares would mean two
    spans and two sets of attributes on every request."""
    from aira_management.config.observability import instrument_django
    from opentelemetry.instrumentation.django import DjangoInstrumentor

    settings.MIDDLEWARE = ["django.middleware.security.SecurityMiddleware"]
    instrumentor = DjangoInstrumentor()
    was_instrumented = instrumentor.is_instrumented_by_opentelemetry
    if was_instrumented:
        instrumentor.uninstrument()
    try:
        instrument_django()
        assert instrument_django() is False
        otel = [name for name in settings.MIDDLEWARE if "opentelemetry" in name]
        assert len(otel) == 1
    finally:
        DjangoInstrumentor().uninstrument()
        if was_instrumented:
            DjangoInstrumentor().instrument()
