import json

from aira_common.logging import configure_logging, get_logger


def test_json_logging_emits_parseable_line(capsys) -> None:
    configure_logging("INFO", json_output=True)
    log = get_logger("test").bind(component="unit")
    log.info("hello", answer=42)

    out = capsys.readouterr().out.strip()
    record = json.loads(out)
    assert record["event"] == "hello"
    assert record["answer"] == 42
    assert record["component"] == "unit"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_level_filtering_suppresses_below_threshold(capsys) -> None:
    configure_logging("WARNING", json_output=True)
    log = get_logger("test")
    log.info("suppressed")
    log.warning("kept")

    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "kept"


def test_console_renderer_runs(capsys) -> None:
    configure_logging("INFO", json_output=False)
    get_logger("test").info("console")
    assert "console" in capsys.readouterr().out


def test_unknown_level_defaults_to_info(capsys) -> None:
    configure_logging("NOPE", json_output=True)
    get_logger("test").info("still-logged")
    assert "still-logged" in capsys.readouterr().out
