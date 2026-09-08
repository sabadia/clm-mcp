"""Domain enums used by the CLM APIs (currently: shipment only — see below).

The OpenAPI specs expose these as bare integers with **no member names or
descriptions** (see PLAN.md "Open items" — `ShipmentStatus` in particular is
0-12 with meanings only the CLM team can confirm). Members below are
labeled `UNKNOWN_<n>` as an explicit, visible placeholder — this is a
deliberate "don't guess" choice (see the global uncertainty policy), not an
oversight. Replace them with real names once available (the CLM front-end
source or a domain expert) and re-run the test suite, which pins today's
int values so a rename can't silently change a filter's behavior.

`resolve_enum_value` lets every curated tool accept either an enum member
name (case-insensitive) or a raw int for these fields, so a caller isn't
blocked by not knowing the (currently unknown) real names.

Construction, team, and konshub have their own named-but-valueless int
fields too (e.g. konshub's `ApprovalStatus`/`CommissionedStatus`,
team's `TeamType`/`RequestType`, construction's `LocationType`/`WKTType`
— see PLAN.md "Other measured facts"). They are deliberately **not** stubbed
here yet: `ShipmentStatus`'s 0-12 range came from an observed live
`GetShipmentsCountForListView` count breakdown (real evidence of its
cardinality), and no equivalent live evidence exists yet for the other
three services' int fields — inventing a placeholder range with no
evidence at all would be a worse violation of the "don't guess" policy than
leaving them unstubbed. `clm_invoke`/`clm_describe_operation` still reach
every one of these fields as a plain int; add real `IntEnum`s here once a
live E2E pass (PLAN.md task 18) or a domain expert confirms their ranges.
"""

from __future__ import annotations

from enum import IntEnum
from http import HTTPStatus


class ShipmentStatus(IntEnum):
    """Shipment lifecycle status (13 values, 0-12).

    The cockpit list view's count breakdown (`GetShipmentsCountForListView`,
    verified against the live API) buckets shipments into Open / Approved /
    Completed / Cancelled-or-Rejected — so these 13 raw values are known to
    collapse into (at least) those four categories, but the exact
    int-to-bucket mapping isn't derivable from the spec alone.
    """

    UNKNOWN_0 = 0
    UNKNOWN_1 = 1
    UNKNOWN_2 = 2
    UNKNOWN_3 = 3
    UNKNOWN_4 = 4
    UNKNOWN_5 = 5
    UNKNOWN_6 = 6
    UNKNOWN_7 = 7
    UNKNOWN_8 = 8
    UNKNOWN_9 = 9
    UNKNOWN_10 = 10
    UNKNOWN_11 = 11
    UNKNOWN_12 = 12


class LeanCardStatus(IntEnum):
    """Lean card (working package) status (4 values, 0-3)."""

    UNKNOWN_0 = 0
    UNKNOWN_1 = 1
    UNKNOWN_2 = 2
    UNKNOWN_3 = 3


class Severity(IntEnum):
    """Incident severity (3 values, 0-2)."""

    UNKNOWN_0 = 0
    UNKNOWN_1 = 1
    UNKNOWN_2 = 2


class ShipmentGroupingType(IntEnum):
    """How shipments are grouped: sender/recipient team, building/floor
    site structure, or working package (4 values, 0-3)."""

    UNKNOWN_0 = 0
    UNKNOWN_1 = 1
    UNKNOWN_2 = 2
    UNKNOWN_3 = 3


class AccessType(IntEnum):
    """Access level (3 values, 0-2)."""

    UNKNOWN_0 = 0
    UNKNOWN_1 = 1
    UNKNOWN_2 = 2


class AvailableDateGetType(IntEnum):
    """Direction for date-availability lookups (3 values, -1/0/1) — likely
    previous/current/next given its use in `GetShipmentWizardTimeslotChangedDate`
    (`direction: next/previous`), but not confirmed."""

    UNKNOWN_NEG_1 = -1
    UNKNOWN_0 = 0
    UNKNOWN_1 = 1


# Enums exposed to `clm_list_enums` / accepted by curated tool parameters
# (request-side domain enums only — HttpStatusCode below is response-side
# and has its own, fully-known helper instead).
DOMAIN_ENUMS: dict[str, type[IntEnum]] = {
    "ShipmentStatus": ShipmentStatus,
    "LeanCardStatus": LeanCardStatus,
    "Severity": Severity,
    "ShipmentGroupingType": ShipmentGroupingType,
    "AccessType": AccessType,
    "AvailableDateGetType": AvailableDateGetType,
}

# Which CLM service each DOMAIN_ENUMS entry belongs to — surfaced by
# `clm_list_enums` (see tools/meta.py::EnumInfo.service) so a caller working
# with a non-shipment service isn't left guessing whether a shipment enum
# applies to it. Every current entry is shipment's; see this module's
# docstring for why the other three services have no entries yet.
DOMAIN_ENUM_SERVICES: dict[str, str] = dict.fromkeys(DOMAIN_ENUMS, "shipment")

# HttpStatusCode (61 values, 100-511) appears only in `CommandResponse` /
# response envelopes, never as a request parameter. Unlike the domain enums
# above, these ARE fully known — they're the public IANA HTTP status code
# registry, not CLM-specific — so this reuses Python's stdlib `HTTPStatus`
# rather than re-transcribing 61 int/name pairs by hand. The one gap is
# 306, reserved-but-unused since HTTP/1.1 and absent from `HTTPStatus`.
_RESERVED_HTTP_STATUS_NAMES: dict[int, str] = {306: "UNUSED"}


def http_status_name(code: int) -> str:
    """Human-readable name for an HTTP status code appearing in a
    `CommandResponse.HttpStatusCode` / `.StatusCode` field."""
    try:
        return HTTPStatus(code).name
    except ValueError:
        return _RESERVED_HTTP_STATUS_NAMES.get(code, f"UNKNOWN_{code}")


def resolve_enum_value[E: IntEnum](enum_cls: type[E], value: E | str | int) -> int:
    """Resolve `value` (an enum member, its name, or a raw int) to the
    int the API expects.

    Accepting a name here is mostly future-proofing until the real
    `UNKNOWN_n` placeholders are replaced — today, `"UNKNOWN_3"` and `3`
    resolve identically. A `ValueError` lists the valid member names.
    """
    if isinstance(value, enum_cls):
        return int(value)
    if isinstance(value, str):
        try:
            return int(enum_cls[value.upper()])
        except KeyError as exc:
            valid = ", ".join(member.name for member in enum_cls)
            raise ValueError(
                f"Unknown {enum_cls.__name__} member {value!r}. Valid names: {valid}"
            ) from exc
    return int(value)
