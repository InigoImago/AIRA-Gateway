"""Where requests may be processed — one policy, every cloud (ADR-0012 §6).

"Which regions may we use" is **one** question with vendor-specific vocabulary (`europe-west1`,
`westeurope`). A per-cloud setting would mean a per-cloud audit, so there is one list, every
transport is measured against it, and every request records the region it went to (`FRD-115`
FR-10). Names are kept flat: Google's and Azure's do not collide, and if two clouds ever do, this
module is the one place to change.

Beside the list, a region may be permitted for **named models alone**
(`AIRA_ALLOWED_MODEL_REGIONS`): a model served only where the list does not reach is then a
decision about that model, and the list keeps meaning what it says for every other one
(`FRD-611`).
"""

from __future__ import annotations

from collections.abc import Mapping

#: Google Cloud regions and multi-regions inside the EU.
EU_REGIONS_GOOGLE = (
    "eu",
    "europe-west1",
    "europe-west3",
    "europe-west4",
    "europe-north1",
)

#: Azure regions inside the EU, listed so the first Azure model is not refused by a default
#: written for one cloud.
EU_REGIONS_AZURE = (
    "westeurope",
    "northeurope",
    "germanywestcentral",
    "swedencentral",
    "francecentral",
)

#: What a deployment permits when it says nothing. Deliberately **not** empty: a residency
#: constraint that has to be switched on is one that will be found switched off.
DEFAULT_ALLOWED_REGIONS = EU_REGIONS_GOOGLE + EU_REGIONS_AZURE


class RegionNotAllowed(Exception):
    """A model is configured in a region this deployment does not permit.

    Raised at startup: failing to boot is the right answer to a configuration that cannot honour
    its own residency claim.
    """


def parse_allowed(configured: str) -> tuple[str, ...]:
    """Read the configured allow-list, falling back to the EU defaults when it is empty."""
    regions = tuple(region.strip() for region in configured.split(",") if region.strip())
    return regions or DEFAULT_ALLOWED_REGIONS


def parse_model_regions(configured: str) -> dict[str, frozenset[str]]:
    """Read the regions permitted for **named models alone** — ``region=model,model;region=model``
    — into the further regions each model may use.

    By model, not by region, because that is the decision being recorded: `global` names no region
    and guarantees none, and permitting it for a model that is served nowhere else must not permit
    it for every model a catalogue entry could later place there (`FRD-611`).

    A malformed entry raises `ValueError` naming it. The settings read it at startup, so a typo is
    a gateway that does not start, never one that refuses the model somebody meant to permit.
    """
    permitted: dict[str, set[str]] = {}
    for entry in (part.strip() for part in configured.split(";")):
        if not entry:
            continue
        region, separator, models = entry.partition("=")
        names = [name.strip() for name in models.split(",") if name.strip()]
        if not separator or not region.strip() or not names:
            raise ValueError(
                f"'{entry}' does not name a region and its models. Expected "
                "'region=model,model', e.g. 'global=glm-4.7-maas,deepseek-v3.2-maas'."
            )
        for name in names:
            permitted.setdefault(name, set()).add(region.strip())
    return {name: frozenset(regions) for name, regions in permitted.items()}


def check_region(
    region: str,
    allowed: tuple[str, ...],
    *,
    model: str = "",
    for_models: Mapping[str, frozenset[str]] | None = None,
) -> None:
    """Refuse ``region`` unless every model may use it, or ``model`` may by name.

    ``model`` is empty where a whole endpoint is checked — an OpenAI server, a Foundry resource,
    AI Studio — and then only the list applies: an exception names a model, never a machine that
    serves many.
    """
    if region in allowed:
        return
    if region in (for_models or {}).get(model, frozenset()):
        return
    named = f" and is not permitted for '{model}' by name" if model else ""
    raise RegionNotAllowed(
        f"Region '{region}' is not in the allowed set {sorted(allowed)}{named}. "
        "Residency is enforced by configuration; widen it deliberately if that is intended."
    )
