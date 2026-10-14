# Load testing the gateway

The question this answers is not "how fast is it". It is **where the ceiling is**: when the system
is slow, is it slow because a model is slow, or because the gateway is? Everything the gateway does
per request — authentication, the read-model lookups, the pipeline, the rate limit, the budget
reservation, the audit row — is work no model ever sees.

The overlay, `deploy/compose/docker-compose.loadtest.yml`, adds the free upstream double
(`modelsim`) and points the gateway at it as one more OpenAI-dialect server. It also empties the
gateway's cloud settings **in its environment** for the duration: the Vertex project, credentials,
API key and models, the Google AI Studio key and models, the Foundry endpoint and deployments, and
the local Ollama — so no adapter for a paid platform is configured from there, and the driver asks
for nothing but the simulated models. **Vault is not touched**, and it ranks above the environment:
a cloud credential held in Vault is not emptied. Run load tests on a stack whose Vault holds none —
with `VAULT_ADDR` empty, the bundled default, nothing comes from Vault at all.

## Run it

```bash
make loadtest-up                     # the double, the gateway pointed at it, the seed
make loadtest SCENARIO=smoke         # ~3 min: every path, end to end
make loadtest SCENARIO=cost          # what one request of each workload costs
make loadtest SCENARIO=ceiling       # ramped until throughput stops rising
make loadtest SCENARIO=capacity      # chat and agentic at 50 / 150 / 300 users, embedding at 4 and 8
make loadtest SCENARIO=beyond        # past the requirement: agentic at 450 / 600, chat at 600 / 900
make loadtest-clean                  # remove the rows, request logs included
make loadtest-down                   # stop the double, restore the gateway's configuration
```

`make loadtest-up` runs `make loadtest-seed`, which writes the `lt-` use cases, keys, budgets, rate
limits and catalogue rows straight into the gateway's read-model; run it on its own to refresh them.
A scenario stops at the first step that saturates, and says so.

`make loadtest` writes `report.md`, rewritten after every step, and **appends** to `results.jsonl`,
both in `docs/measurements/` — `MEASUREMENTS=` puts them somewhere else. A second run therefore adds
its steps to the first one's `.jsonl`; [`docs/measurements/README.md`](../../docs/measurements/README.md)
says how the kept runs are named.

What the overlay reads, all optional:

| Variable | Default | What it sets |
| --- | --- | --- |
| `AIRA_GATEWAY_WORKERS` | `1` | uvicorn workers in the gateway — the image ships one |
| `AIRA_LOADTEST_GATEWAY_CPUS` | `0` (no limit) | the cores the gateway's container may use |
| `AIRA_GATEWAY_UVICORN_EXTRA` | empty | further uvicorn options, for a run whose difference is named |
| `AIRA_LOADTEST_SIM_WORKERS` | `2` | processes serving the double, so it is not the ceiling |
| `MODELSIM_EXTRA_LATENCY` | `0` | seconds the double adds to every response, on top of what each model declares |
| `AIRA_LOADTEST_MODELSIM_PORT` | `8099` | where the double is published |

## The pieces

| File | What it is |
| --- | --- |
| `models.py` | the simulated models: declared time to first token, token rate, prices. One declaration, read by the double, the seeder and the driver. |
| `modelsim.py` | the double. A plain ASGI app speaking the OpenAI dialect, with pre-encoded SSE events and a deadline-based clock. |
| `seed.py` | the `lt-` use cases, keys, budgets, rate limits and catalogue rows, written into the gateway's read-model. `--clean` removes exactly those. |
| `driver.py` | the load, spread over processes. Seven profiles — `agentic`, `agentic-thoughts`, `chat`, `chat-batched`, `embed`, `bare`, `bare-stream` — each driven at the gateway and at the double. |
| `sample.py` | CPU from the cgroups, memory from `docker stats`, the gateway's warnings, the database's connections. |
| `run.py` | one scenario: baseline, measured load, verdict, report. |
| `addresses.py` | where to connect, asked of `tools/stack_addresses.py` rather than written down. |

## Three things that make the numbers mean something

**The double declares its latency, and the driver subtracts it.** `overhead` is the request's wall
clock less what the model promised the answer would take. Without that subtraction every figure is
a statement about the double.

**Every step runs twice.** Once at the gateway, once straight at the double from the same driver at
the same concurrency. If the baseline is already slow, the run is measuring the driver, and the
verdict says so instead of blaming the gateway.

**CPU comes from the cgroup, not from `docker stats`.** During one step `docker stats` reported the
gateway at 21% while its own `cpu.stat` showed 0.91 cores — a process out of CPU, reported as idle.
Every conclusion drawn from the first number was wrong in the same direction.

## What a run leaves behind

Rows. The seed's use cases and keys are **active** credentials in a real read-model, and the
traffic writes real audit rows against them. `make loadtest-clean` removes both; a load run left in
the audit trail is a lie about what the installation did.
