# Measurements

Raw output from load runs (`make loadtest`, [`FRD-136`](../features/FRD-136-capacity-the-gateway-is-not-the-bottleneck.md)).
What the numbers mean and what follows from them is in that FRD's §13 and, for a reader sizing a
deployment, in [`DEPLOYMENT.md`](../DEPLOYMENT.md) §1a.

These files are kept because a capacity claim with no run behind it is an opinion. They are **not**
a baseline anything asserts against: they were taken on one eight-core machine that was also
running the stack, the double and the driver, so the absolute throughput is about that machine. The
figures that travel to another one are per request and per core.

| Run | What it was |
| --- | --- |
| `2026-09-20-capacity-one-worker.*` | the gateway as the image ships it — `uvicorn` with no `--workers` |
| `2026-09-20-capacity-four-workers.*` | the same eight steps with `--workers 4` |

`.md` is the report; `.jsonl` is one JSON object per step, with the baseline run, the per-container
CPU from the cgroups, the memory, the gateway's warnings and the database's connection counts.

To take a fresh set:

```bash
make loadtest-up
make loadtest SCENARIO=capacity MEASUREMENTS=docs/measurements
make loadtest-clean && make loadtest-down
```
