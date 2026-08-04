from aira_gateway.pipeline.config import Pipeline, StepType


def test_from_dict_parses_steps_and_fallbacks() -> None:
    pipeline = Pipeline.from_dict(
        {
            "steps": [
                {"type": "injection_filter", "config": {"mode": "heuristic"}},
                {"type": "model_route", "config": {"rules": []}},
            ],
            "fallback_models": ["b", "", "c"],
        }
    )
    assert [s.type for s in pipeline.steps] == [StepType.INJECTION_FILTER, StepType.MODEL_ROUTE]
    assert pipeline.steps[0].config == {"mode": "heuristic"}
    assert pipeline.fallback_models == ("b", "c")
    assert pipeline.is_empty is False


def test_from_dict_skips_unknown_or_malformed_steps() -> None:
    pipeline = Pipeline.from_dict(
        {"steps": [{"type": "future_step"}, {"no_type": 1}, {"type": "allow_check"}]}
    )
    assert [s.type for s in pipeline.steps] == [StepType.ALLOW_CHECK]


def test_empty_pipeline() -> None:
    assert Pipeline.from_dict({}).is_empty is True
