"""Topic names for a stage on a shared cluster.

Several stages (T, F, Q) share one Kafka cluster, so each installation names its topics with its
stage: `aira.t.usecases`. The stage follows `aira.`, so one prefixed ACL covers one stage. Without a
stage the names are today's, which is what every existing installation keeps.
"""

from __future__ import annotations

import pytest

from aira_common.kafka import (
    CONFIG_TOPICS,
    CONSUMER_GROUP,
    USECASE_TOPIC,
    KafkaStageError,
    consumer_group,
    staged,
    validate_stage,
)


def test_without_a_stage_every_name_is_todays() -> None:
    assert [staged(topic, "") for topic in CONFIG_TOPICS] == list(CONFIG_TOPICS)
    assert consumer_group("") == CONSUMER_GROUP == "aira-gateway"


def test_a_stage_follows_aira_in_every_topic_and_in_the_group() -> None:
    assert staged(USECASE_TOPIC, "t") == "aira.t.usecases"
    assert all(staged(topic, "t").startswith("aira.t.") for topic in CONFIG_TOPICS)
    assert staged("aira.anomaly-rules", "q") == "aira.q.anomaly-rules"
    assert consumer_group("t") == "aira-gateway.t"


def test_two_stages_never_share_a_topic() -> None:
    assert not {staged(t, "t") for t in CONFIG_TOPICS} & {staged(t, "q") for t in CONFIG_TOPICS}


@pytest.mark.parametrize("stage", ["T", "t.1", "t-1", "abcdefghijklmnopq", "t q", "ä"])
def test_a_stage_that_cannot_be_one_name_segment_is_refused(stage: str) -> None:
    with pytest.raises(KafkaStageError):
        validate_stage(stage)
    with pytest.raises(KafkaStageError):
        staged(USECASE_TOPIC, stage)


def test_surrounding_space_is_not_a_second_stage() -> None:
    assert staged(USECASE_TOPIC, " t ") == "aira.t.usecases"
    assert validate_stage("  ") == ""
