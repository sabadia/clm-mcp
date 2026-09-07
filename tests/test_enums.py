"""Unit tests for enums.py: name/int resolution and the HTTP status helper."""

from __future__ import annotations

import pytest

from clm_mcp.enums import (
    DOMAIN_ENUMS,
    ShipmentStatus,
    http_status_name,
    resolve_enum_value,
)


def test_domain_enums_values_pinned() -> None:
    """Pins today's known-correct int ranges — a future rename to real
    member names must not change these values."""
    assert [m.value for m in DOMAIN_ENUMS["ShipmentStatus"]] == list(range(13))
    assert [m.value for m in DOMAIN_ENUMS["LeanCardStatus"]] == list(range(4))
    assert [m.value for m in DOMAIN_ENUMS["Severity"]] == list(range(3))
    assert [m.value for m in DOMAIN_ENUMS["ShipmentGroupingType"]] == list(range(4))
    assert [m.value for m in DOMAIN_ENUMS["AccessType"]] == list(range(3))
    assert [m.value for m in DOMAIN_ENUMS["AvailableDateGetType"]] == [-1, 0, 1]


def test_resolve_enum_value_accepts_member() -> None:
    assert resolve_enum_value(ShipmentStatus, ShipmentStatus.UNKNOWN_3) == 3


def test_resolve_enum_value_accepts_name_case_insensitive() -> None:
    assert resolve_enum_value(ShipmentStatus, "unknown_3") == 3
    assert resolve_enum_value(ShipmentStatus, "UNKNOWN_3") == 3


def test_resolve_enum_value_accepts_raw_int() -> None:
    assert resolve_enum_value(ShipmentStatus, 7) == 7


def test_resolve_enum_value_unknown_name_lists_valid_members() -> None:
    with pytest.raises(ValueError, match="UNKNOWN_0"):
        resolve_enum_value(ShipmentStatus, "not_a_real_status")


def test_http_status_name_known_codes() -> None:
    assert http_status_name(200) == "OK"
    assert http_status_name(404) == "NOT_FOUND"
    assert http_status_name(500) == "INTERNAL_SERVER_ERROR"


def test_http_status_name_reserved_306() -> None:
    assert http_status_name(306) == "UNUSED"


def test_http_status_name_unknown_code_falls_back() -> None:
    assert http_status_name(999) == "UNKNOWN_999"
