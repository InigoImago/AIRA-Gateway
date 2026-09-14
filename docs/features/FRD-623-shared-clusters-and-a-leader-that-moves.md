# FRD-623 — Shared clusters, and a Redis leader that moves

> Phase: 7 · Status: **Done** · Owner: Vadim Scheibe
>
> Origin: operations prepared stage T. Kafka runs as one development and one production cluster,
> so stages T, F and Q share a cluster. Redis runs as Redis Sentinel: a leader and followers, with
> the leader moved to another server automatically, for example when one is restarted. Operations
> asked whether AIRA's topics should carry the stage, and whether AIRA's Redis client supports
> Sentinel.
>
> Related: [`FRD-204`](FRD-204-config-distribution-kafka.md) (configuration over Kafka),
> [`ADR-0008`](../adr/ADR-0008-redis-shared-counters.md) (shared counters),
> [`FRD-405`](FRD-405-rate-limiting.md) (what degrades without Redis),
> [`ADR-0018`](../adr/ADR-0018-everything-between-the-services-is-a-trust-boundary.md).

## 1. Why

- **Kafka.** The gateway builds its read model, including its authorization, from the
  configuration topics. On a shared cluster, two stages that use the same names read and write each
  other's configuration. Separating stages by ACL needs a name one ACL can cover.
- **Redis.** The gateway connected to one address. When a Sentinel moves the leader, that address
  becomes a follower and refuses writes, or disappears. Rate limits and budgets would degrade until
  somebody changed the configuration.

## 2. Requirements

- **FR-1 — A stage in every name.** `AIRA_KAFKA_STAGE`, read by both planes, names every topic
  `aira.<stage>.<entity>` and the gateway's consumer group `aira-gateway.<stage>`. The stage follows
  `aira.`, so one prefixed ACL covers one stage, including topics added later. Empty keeps today's
  names.
- **FR-2 — Refused at start-up.** A stage is one to sixteen lower-case letters or digits; anything
  else refuses the settings. A typo must not create a third set of topics.
- **FR-3 — One list.** Management publishes to, and the gateway subscribes to, one list of
  configuration topics (`aira_common.kafka.CONFIG_TOPICS`). The compose stack and the Makefile
  create them for the stage.
- **FR-4 — Sentinel.** `AIRA_REDIS_SENTINELS` and `AIRA_REDIS_SENTINEL_SERVICE` replace
  `AIRA_REDIS_URL`: the gateway asks the sentinels for the leader and follows it on failover.
  - An ACL user, a data password, a sentinel password and a database number are supported.
  - The passwords are secrets, for Vault, and are refused in a configuration file.
  - Sentinels without the leader's name, or an address that is not `host:port`, refuse to start.
- **FR-5 — Degradation unchanged.** While the leader moves, the gateway behaves as for any Redis
  outage: rate limits hold per instance, budgets use the Postgres path, readiness reports it.

## 3. Testing

- **Kafka stage:**
  - Hermetic tests cover the names, the group, the refusal, Management's outbox and the gateway's
    subscriptions.
  - The topic-creation guard compares the base names in both loops with the code.
  - Mutations KS1–KS5.
- **Sentinel:**
  - Hermetic tests cover the parsing, the refusals, a log target without a password, and a client
    asked of the sentinels with the credentials.
  - Mutations SN1–SN3.
- **Live** (DEVLOG 2026-09-14):
  - **Stage `t`.** A use case went to `aira.t.usecases` only, and group `aira-gateway.t` applied
    it within 7 s.
  - **Sentinel** (a leader, a follower, three sentinels). Stopping the leader moved it in 4 s.
    Requests kept answering 200 with no fallback reported, and the old leader rejoined as a
    follower.
