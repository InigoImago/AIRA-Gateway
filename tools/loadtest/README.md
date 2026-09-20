# Load testing the gateway (`FRD-136`)

The question this answers is not "how fast is it". It is **where the ceiling is**: when the system
is slow, is it slow because a model is slow, or because the gateway is? Everything the gateway does
per request — authentication, the read-model lookups, the pipeline, the rate limit, the budget
reservation, the audit row — is work no model ever sees.

Nothing here contacts a cloud model, and the overlay makes that structural rather than careful:
while a run is set up, the gateway has **no** Vertex, Google or Foundry credentials at all.

## Run it

```bash
make loadtest-up                     # the double, the gateway pointed at it, the seed
make loadtest SCENARIO=smoke         # ~3 min: every path, end to end
make loadtest SCENARIO=cost          # what one request of each workload costs
make loadtest SCENARIO=ceiling       # ramped until throughput stops rising
make loadtest SCENARIO=capacity      # the three real workloads at 50 / 150 / 300
make loadtest-clean                  # remove the rows, request logs included
make loadtest-down                   # stop the double, restore the gateway's configuration
```

`make loadtest` writes `docs/measurements/report.md` and `results.jsonl`. Set `MEASUREMENTS=` to
put them somewhere else, and `AIRA_GATEWAY_WORKERS=4 make loadtest-up` to measure a gateway with
more than the one uvicorn worker the image ships with.

## The pieces

| File | What it is |
| --- | --- |
| `models.py` | the simulated models: declared time to first token, token rate, prices. One declaration, read by the double, the seeder and the driver. |
| `modelsim.py` | the double. A plain ASGI app speaking the OpenAI dialect, with pre-encoded SSE events and a deadline-based clock. |
| `seed.py` | the `lt-` use cases, keys, budgets, rate limits and catalogue rows, written into the gateway's read-model. `--clean` removes exactly those. |
| `driver.py` | the load, spread over processes. Four profiles, each driven at the gateway and at the double. |
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
