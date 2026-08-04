from aira_common.kafka import USECASE_TOPIC, InMemoryProducer, KafkaRecord


async def test_in_memory_producer_records() -> None:
    producer = InMemoryProducer()
    await producer.start()
    record = KafkaRecord(
        topic=USECASE_TOPIC, key="uc", event_type="usecase.upserted", payload={"slug": "uc"}
    )
    await producer.send(record)
    await producer.stop()
    assert producer.sent == [record]
