"""One load run: the baseline, the measured load, and what the machine was doing (`FRD-136`).

Every step is driven twice — once straight at the double and once through the gateway — because a
figure about the gateway that was never compared with the same load *without* the gateway is a
figure about whatever was slowest that afternoon.

    uv run python -m tools.loadtest.run --scenario smoke
    uv run python -m tools.loadtest.run --scenario capacity --out docs/measurements

Stops early when a step fails its condition (`FRD-136` FR-7): more than one request in fifty
refused or dropped, or a median overhead above the ceiling named in the scenario. Saturation is
then a number in the report rather than a guess.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from tools.loadtest import driver, sample

#: What is watched while a step runs. The stack's own names, prefixed as Compose prefixes them.
STACK = os.environ.get("AIRA_STACK", "aira")
WATCHED = [
    f"{STACK}-gateway",
    f"{STACK}-modelsim",
    f"{STACK}-postgres",
    f"{STACK}-redis",
    f"{STACK}-kafka",
]

#: A step is a failure when more than this share of its requests did not complete. Two per cent:
#: high enough that one connection reset does not end a run, low enough that a gateway shedding
#: load is not called healthy.
MAX_FAILURE_SHARE = 0.02


@dataclass
class Step:
    """One measurement: a profile at a concurrency, for a duration."""

    profile: str
    users: int
    duration: float
    #: The median overhead, in milliseconds, above which this step counts as saturated. `None`
    #: leaves the failure share as the only condition — right for a step whose purpose is to find
    #: where the ceiling is rather than to assert one.
    overhead_ceiling_ms: float | None = None


@dataclass
class StepResult:
    step: dict[str, Any]
    baseline: dict[str, Any]
    gateway: dict[str, Any]
    #: CPU seconds and cores per container, from the cgroups. The figure the capacity statement
    #: is built on: cores divided by requests is what one request costs, and that number is the
    #: only one that travels to a machine other than this one.
    cpu: dict[str, dict[str, float]] = field(default_factory=dict)
    containers: dict[str, dict[str, float]] = field(default_factory=dict)
    gateway_warnings: dict[str, int] = field(default_factory=dict)
    postgres: dict[str, Any] = field(default_factory=dict)
    verdict: str = ""


#: Named scenarios. Each is a list of steps, run in order, and stopping when one gives way.
SCENARIOS: dict[str, list[Step]] = {
    # Two minutes, enough to see that every path works end to end before spending an hour.
    "smoke": [
        Step("bare", users=8, duration=20),
        Step("chat", users=8, duration=20),
        Step("agentic", users=8, duration=30),
        Step("embed", users=2, duration=20),
    ],
    # What one request costs, per workload, at a concurrency **below** saturation. Cost per
    # request is what travels to another machine; a throughput number is about this laptop.
    "cost": [
        Step("bare", users=16, duration=30),
        Step("bare-stream", users=16, duration=30),
        Step("chat", users=40, duration=45),
        Step("chat-batched", users=40, duration=45),
        Step("agentic", users=40, duration=60),
        Step("embed", users=3, duration=30),
    ],
    # And the same, ramped until it stops scaling — the per-core throughput figure.
    "ceiling": [
        Step("bare", users=16, duration=30),
        Step("bare", users=48, duration=30),
        Step("bare", users=96, duration=30),
        Step("bare-stream", users=16, duration=30),
        Step("bare-stream", users=48, duration=30),
        Step("bare-stream", users=96, duration=30),
    ],
    # The owner's question: the three real workloads at the concurrencies the installation will
    # carry. The overhead ceiling is 250 ms — a quarter of the double's own time to first token,
    # which is the point at which a person would start attributing the wait to the gateway.
    "capacity": [
        Step("chat", users=50, duration=60, overhead_ceiling_ms=250),
        Step("chat", users=150, duration=60, overhead_ceiling_ms=250),
        Step("chat", users=300, duration=60, overhead_ceiling_ms=250),
        Step("agentic", users=50, duration=60, overhead_ceiling_ms=250),
        Step("agentic", users=150, duration=60, overhead_ceiling_ms=250),
        Step("agentic", users=300, duration=60, overhead_ceiling_ms=250),
        Step("embed", users=4, duration=45),
        Step("embed", users=8, duration=45),
    ],
    # Past the stated requirement, to find where it actually gives way.
    "beyond": [
        Step("agentic", users=450, duration=60),
        Step("agentic", users=600, duration=60),
        Step("chat", users=600, duration=60),
        Step("chat", users=900, duration=60),
    ],
}


def _processes(users: int) -> int:
    """How many driver processes one step gets.

    Enough that no single process is holding more than about eighty streams, capped by the cores
    this machine can spare after the stack has had its share.
    """
    cores = os.cpu_count() or 4
    return max(1, min(max(1, cores // 2), (users + 79) // 80))


def run_step(step: Step, *, timeout: float) -> StepResult:
    processes = _processes(step.users)

    baseline = driver.run(
        step.profile,
        users=step.users,
        duration=step.duration,
        target="direct",
        processes=processes,
        timeout=timeout,
    )

    since = sample.now_stamp()
    with sample.Sampler(WATCHED) as sampler, sample.Cpu(WATCHED) as cpu:
        measured = driver.run(
            step.profile,
            users=step.users,
            duration=step.duration,
            target="gateway",
            processes=processes,
            timeout=timeout,
        )
        connections = sample.postgres_connections(f"{STACK}-postgres", "aira_gateway")
    result = StepResult(
        step=asdict(step),
        baseline=asdict(baseline),
        gateway=asdict(measured),
        cpu=cpu.summary(measured.completed + measured.failed),
        containers=sampler.summary(),
        gateway_warnings=sample.gateway_warnings(f"{STACK}-gateway", since),
        postgres=connections,
    )
    result.verdict = _verdict(step, measured, baseline)
    return result


def _verdict(step: Step, measured: driver.Result, baseline: driver.Result) -> str:
    """Why a step counts as passed or as the ceiling. A sentence, because a boolean here would be
    read as "the gateway is fine" by whoever skims the report."""
    attempted = measured.completed + measured.failed
    share = measured.failed / attempted if attempted else 1.0
    if attempted == 0:
        return "no requests completed — the step did not run"
    if share > MAX_FAILURE_SHARE:
        top = next(iter(measured.failures), "?")
        return f"saturated: {share:.1%} of requests failed, most often '{top}'"
    if measured.driver_cores > 0.85 * _processes(step.users):
        return (
            f"inconclusive: the driver used {measured.driver_cores:.2f} cores across "
            f"{_processes(step.users)} processes, so this measures the driver"
        )
    overhead = measured.overhead.get("p50_ms", 0.0)
    if step.overhead_ceiling_ms is not None and overhead > step.overhead_ceiling_ms:
        return (
            f"saturated: median overhead {overhead:.0f} ms above the "
            f"{step.overhead_ceiling_ms:.0f} ms ceiling"
        )
    baseline_overhead = baseline.overhead.get("p50_ms", 0.0)
    return (
        f"passed: median overhead {overhead:.0f} ms "
        f"({baseline_overhead:.0f} ms of it without the gateway)"
    )


def report(results: list[StepResult]) -> str:
    lines = [
        "# Load run",
        "",
        f"Taken {time.strftime('%Y-%m-%d %H:%M')} on {os.cpu_count()} cores.",
        "",
        "`overhead` is the request's wall clock less what the double promised the answer would",
        "take. The baseline column is the same load with the gateway taken out of the path, so",
        "whatever it shows is the driver and the loopback, not the system under test.",
        "",
        "| profile | users | rps | ok | failed | p50 ms | p99 ms | overhead p50 |"
        " overhead p99 | gw cores | gw CPU ms/req | pg CPU ms/req | verdict |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for result in results:
        gateway = result.gateway
        gateway_cpu = result.cpu.get(f"{STACK}-gateway", {})
        postgres_cpu = result.cpu.get(f"{STACK}-postgres", {})
        lines.append(
            "| {profile} | {users} | {rps} | {ok} | {failed} | {p50} | {p99} | {op50} | {op99} |"
            " {cores} | {cpureq} | {pgreq} | {verdict} |".format(
                profile=gateway["profile"],
                users=gateway["users"],
                rps=gateway["rps"],
                ok=gateway["completed"],
                failed=gateway["failed"],
                p50=gateway["latency"].get("p50_ms", "—"),
                p99=gateway["latency"].get("p99_ms", "—"),
                op50=gateway["overhead"].get("p50_ms", "—"),
                op99=gateway["overhead"].get("p99_ms", "—"),
                cores=gateway_cpu.get("cores", "—"),
                cpureq=gateway_cpu.get("cpu_ms_per_request", "—"),
                pgreq=postgres_cpu.get("cpu_ms_per_request", "—"),
                verdict=result.verdict,
            )
        )
    lines.append("")
    for result in results:
        if result.gateway_warnings or result.gateway["failures"]:
            lines.append(
                f"**{result.gateway['profile']} at {result.gateway['users']}** — failures: "
                f"{json.dumps(result.gateway['failures'])}; gateway warnings: "
                f"{json.dumps(result.gateway_warnings)}"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="smoke", choices=sorted(SCENARIOS))
    parser.add_argument("--out", default="", help="directory for results.jsonl and report.md")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="run every step even after one saturates",
    )
    arguments = parser.parse_args()

    destination = Path(arguments.out) if arguments.out else None
    if destination:
        destination.mkdir(parents=True, exist_ok=True)

    results: list[StepResult] = []
    for step in SCENARIOS[arguments.scenario]:
        print(f"==> {step.profile} at {step.users} users for {step.duration}s", flush=True)
        result = run_step(step, timeout=arguments.timeout)
        results.append(result)
        print(f"    {result.verdict}", flush=True)
        print(
            f"    rps={result.gateway['rps']} p50={result.gateway['latency'].get('p50_ms')}ms "
            f"overhead_p50={result.gateway['overhead'].get('p50_ms')}ms "
            f"gateway={result.cpu.get(f'{STACK}-gateway', {})} "
            f"driver_cores={result.gateway['driver_cores']}",
            flush=True,
        )
        if destination:
            with (destination / "results.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(result)) + "\n")
            (destination / "report.md").write_text(report(results), encoding="utf-8")
        if result.verdict.startswith("saturated") and not arguments.keep_going:
            print("    stopping: this scenario has found its ceiling", flush=True)
            break

    print()
    print(report(results))


if __name__ == "__main__":
    main()
