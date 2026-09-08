"""The catalog of CLM business services this server wraps.

A single source of truth for "what is a CLM service" — deliberately its own
top-level module rather than living in `config.py` (would create a circular
import with `spec/registry.py`, which needs `SERVICES` to load specs) or in
`services/` (that package is the business-composition layer per service; a
module named `services_catalog` next to it would be confusing if it lived
inside).

See PLAN.md's "Verified facts" section for how these four values were
measured: all four specs share the exact same path shape
(`/{title}/{Tag}/{Operation}`, always 3 segments), so `path_prefix` doubles as
a build-time assertion in `spec/registry.py` — if a future spec refresh
changes that shape, registry construction fails loudly instead of routing
silently to a 404.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class ClmService:
    """One CLM business service: its spec file, API gateway, and path shape."""

    slug: str
    spec_filename: str
    gateway_segment: str
    path_prefix: str
    title: str


_SHIPMENT = ClmService(
    slug="shipment",
    spec_filename="shipment.json",
    gateway_segment="business-clm-shipment",
    path_prefix="/ClmShipmentWebService",
    title="ClmShipmentWebService",
)
_CONSTRUCTION = ClmService(
    slug="construction",
    spec_filename="construction.json",
    gateway_segment="business-clm-construction",
    path_prefix="/ClmConstructionWebService",
    title="ClmConstructionWebService",
)
_TEAM = ClmService(
    slug="team",
    spec_filename="team.json",
    gateway_segment="business-clm-team",
    path_prefix="/ClmTeamWebService",
    title="ClmTeamWebService",
)
_KONSHUB = ClmService(
    slug="konshub",
    spec_filename="konshub.json",
    gateway_segment="business-clm-konshub",
    path_prefix="/ClmKonshubWebService",
    title="ClmKonshubWebService",
)

# Insertion-ordered: this is also the order services are merged into the
# registry and the order `clm_list_operations`/`clm_list_enums` group by.
SERVICES: Final[dict[str, ClmService]] = {
    s.slug: s for s in (_SHIPMENT, _CONSTRUCTION, _TEAM, _KONSHUB)
}

# The one service that predates this catalog — kept as the default so
# `Settings.api_base_url` (a pre-existing, unqualified setting) has an
# unambiguous service to apply to. See `config.py::Settings.base_url_for`.
DEFAULT_SERVICE: Final = "shipment"
