"""Where requests may be processed — one policy, every cloud (ADR-0012 §6).

"Which regions are we allowed to use" is **one** question with a vendor-specific vocabulary, not
one question per vendor. Google says `europe-west1` and `eu`; Azure says `westeurope` and
`germanywestcentral`. The names differ, the policy does not.

That matters more than it looks. A per-cloud setting means a per-cloud audit: somebody checking
whether this installation can leave the EU has to find, read and reconcile one list per platform,
and the one that was added last is the one nobody remembers to check. So there is one list, every
transport is measured against it, and every request records the region it actually went to
(`FRD-115` FR-10).

The names are kept **flat** rather than qualified as `provider:region`. Google's and Azure's region
names do not collide, and an operator thinks in "which EU regions may we use", not in a matrix.
Should two clouds ever choose the same name for different places, this is the module that has to
change — and one module is the point.
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

#: Azure regions inside the EU. Listed **before** Microsoft Foundry exists (`FRD-120`) on purpose:
#: the alternative is that the first Azure model added is silently refused by a default nobody
#: thought to widen, which is a bad way to learn that a policy list was written for one cloud.
EU_REGIONS_AZURE = (
    "westeurope",
    "northeurope",
    "germanywestcentral",
    "swedencentral",
    "francecentral",
)

#: What a deployment permits when it says nothing. Deliberately **not** empty: an empty default
#: would mean "no residency constraint" in a product whose reason for existing includes one, and a
#: constraint that has to be switched on is a constraint that will be found switched off.
DEFAULT_ALLOWED_REGIONS = EU_REGIONS_GOOGLE + EU_REGIONS_AZURE


class RegionNotAllowed(Exception):
    """A model is configured in a region this deployment does not permit.

    Raised at startup. Failing to boot is the correct response to a configuration that cannot
    honour its own residency claim; a running gateway that sometimes leaves the EU is not.
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
