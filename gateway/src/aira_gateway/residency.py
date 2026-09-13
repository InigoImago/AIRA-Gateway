"""Where requests may be processed — one policy, every cloud (ADR-0012 §6).

"Which regions may we use" is **one** question with vendor-specific vocabulary (`europe-west1`,
`westeurope`). A per-cloud setting would mean a per-cloud audit, so there is one list, every
transport is measured against it, and every request records the region it went to (`FRD-115`
FR-10). Names are kept flat: Google's and Azure's do not collide, and if two clouds ever do, this
module is the one place to change.
"""

from __future__ import annotations

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


def check_region(region: str, allowed: tuple[str, ...]) -> None:
    if region not in allowed:
        raise RegionNotAllowed(
            f"Region '{region}' is not in the allowed set {sorted(allowed)}. "
            "Residency is enforced by configuration; widen it deliberately if that is intended."
        )
