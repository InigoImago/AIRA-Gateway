"""The seed and the gateway read `AIRA_OPENAI_SERVERS` alike (`FRD-123`).

Management's seed asks the first declared server which models it serves; the gateway serves them.
The seed restates the gateway's reading of the list — the gateway is not installed beside the
management plane — and a restatement nothing compares drifts: the seed kept the spaces the gateway
trims, `urlopen` refused the padded URL, and the seed declared nothing for an endpoint that
answered. Compared here on the shapes an operator actually writes.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
for path in (
    ROOT / "gateway" / "src",
    ROOT / "libs" / "src",
    ROOT / "management" / "backend" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from aira_management.apps.seed.contributions.local_models import first_server_url  # noqa: E402

from aira_gateway.upstreams.openai import parse_servers  # noqa: E402

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "spec",
    [
        "gpu-a=http://gpu-a:11434|qwen3:8b",
        "gpu-a = http://gpu-a:11434 | qwen3:8b",
        "  gpu-a=http://gpu-a:11434|qwen3:8b|nomic-embed-text|dc-frankfurt ;",
        ";gpu-a=http://gpu-a:11434|qwen3:8b;gpu-b=http://gpu-b:11434|llama3.1:70b||dc-berlin",
        "gpu-a=https://gpu-a.example:443/base|qwen3:8b",
    ],
)
def test_the_seed_asks_the_server_the_gateway_serves(spec: str) -> None:
    assert first_server_url(spec) == parse_servers(spec)[0].url
