# Measurements

Raw output from load runs (`make loadtest`). How a run is taken, and why its figures are the
gateway's own cost rather than the model's, is in
[`tools/loadtest`](../../tools/loadtest/README.md); what the numbers mean for a deployment, and what
follows from them, is in [`DEPLOYMENT.md`](../DEPLOYMENT.md) §1a.

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
make loadtest-up                                   # AIRA_GATEWAY_WORKERS=4 make loadtest-up for four
make loadtest SCENARIO=capacity MEASUREMENTS=docs/measurements
make loadtest-clean && make loadtest-down
```

A run writes `report.md` — rewritten after every step — and **appends** one line per step to
`results.jsonl`, both in the `MEASUREMENTS` directory (`docs/measurements` by default). Rename the
pair to the dated form above before the next run, or the next run's steps join this one's in the
same `.jsonl`.
