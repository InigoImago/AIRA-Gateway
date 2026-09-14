"""The Kafka stage and Redis Sentinel, as the gateway reads them.

A stage names every topic and the consumer group, on a cluster several stages share; Sentinel names
the leader of a Redis that moves on failover. A wrong value for either refuses to start, because a
gateway reading the wrong topics or no counter store at all fails quietly.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aira_common.counters import SentinelConfigError
from aira_common.kafka import CONFIG_TOPICS, consumer_group
from aira_gateway.app import create_app
from aira_gateway.config import GatewaySettings
from aira_gateway.consumer.worker import subscriptions


def test_the_consumer_subscribes_every_topic_of_its_stage_under_its_own_group() -> None:
    topics = subscriptions("t")
    assert len(topics) == len(CONFIG_TOPICS)
    assert all(topic.startswith("aira.t.") for topic in topics)
    assert subscriptions("") == CONFIG_TOPICS
    assert consumer_group(GatewaySettings(kafka_stage="t").kafka_stage) == "aira-gateway.t"


def test_a_stage_that_cannot_be_a_name_segment_refuses_the_settings() -> None:
    with pytest.raises(ValidationError):
        GatewaySettings(kafka_stage="T.1")


def test_without_sentinels_the_url_is_used() -> None:
    assert GatewaySettings(redis_sentinels="").redis_sentinel() is None


def test_sentinels_with_the_leaders_name_replace_the_url() -> None:
    config = GatewaySettings(
        redis_sentinels="redis-a:26379, redis-b:26380",
        redis_sentinel_service="aira",
        redis_username="aira",
        redis_password="data-secret",
        redis_sentinel_password="sentinel-secret",
        redis_db=2,
    ).redis_sentinel()

    assert config is not None
    assert config.sentinels == (("redis-a", 26379), ("redis-b", 26380))
    assert (config.service, config.username, config.db) == ("aira", "aira", 2)
    assert (config.password, config.sentinel_password) == ("data-secret", "sentinel-secret")


def test_a_bad_sentinel_address_refuses_the_settings() -> None:
    with pytest.raises(ValidationError):
        GatewaySettings(redis_sentinels="redis-a")


def test_sentinels_without_the_leaders_name_refuse_to_start() -> None:
    with pytest.raises(SentinelConfigError):
        create_app(GatewaySettings(auth_required=False, redis_sentinels="redis-a:26379"))
