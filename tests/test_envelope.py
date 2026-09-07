"""Unit tests for unwrap_envelope: both response shapes, and the core
invariant that the CLM API signals failure in the body, not the HTTP status.
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from clm_mcp.api.envelope import extract_total_count, unwrap_envelope

# --------------------------------------------------------------------------
# Query envelope: {Data, IsSuccess, StatusCode, ErrorMessage, PropertyName,
#                  ValidationErrors, TotalCount}
# --------------------------------------------------------------------------


def test_query_envelope_success_returns_data() -> None:
    payload: dict[str, Any] = {
        "Data": [{"Id": "1"}],
        "IsSuccess": True,
        "StatusCode": 0,
        "ErrorMessage": None,
        "PropertyName": None,
        "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "TotalCount": 1,
    }
    result = unwrap_envelope(payload, operation_name="ShipmentQuery/GetShipmentsForListView")
    assert result == [{"Id": "1"}]


def test_query_envelope_is_success_false_raises_tool_error() -> None:
    payload: dict[str, Any] = {
        "Data": None,
        "IsSuccess": False,
        "StatusCode": 404,
        "ErrorMessage": "Shipment not found.",
        "PropertyName": None,
        "ValidationErrors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "TotalCount": 0,
    }
    with pytest.raises(ToolError, match="Shipment not found"):
        unwrap_envelope(payload, operation_name="ShipmentQuery/GetShipmentById")


def test_query_envelope_validation_failure_raises_tool_error_with_property_name() -> None:
    payload: dict[str, Any] = {
        "Data": None,
        "IsSuccess": True,  # deliberately True — the ValidationErrors flag alone must still fail it
        "StatusCode": 400,
        "ErrorMessage": None,
        "PropertyName": "SiteId",
        "ValidationErrors": {
            "IsValid": False,
            "Errors": ["SiteId must not be empty."],
            "RuleSetsExecuted": None,
        },
        "TotalCount": 0,
    }
    with pytest.raises(ToolError, match=r"SiteId must not be empty.*field: SiteId"):
        unwrap_envelope(payload, operation_name="ShipmentQuery/GetShipmentsForListView")


# --------------------------------------------------------------------------
# Command envelope: {RequestUri, ExternalError, HttpStatusCode, Errors,
#                     ErrorMessages, StatusCode}
# --------------------------------------------------------------------------


def test_command_envelope_success_returns_whole_body() -> None:
    payload: dict[str, Any] = {
        "RequestUri": "https://example/api/x",
        "ExternalError": None,
        "HttpStatusCode": 200,
        "Errors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "ErrorMessages": [],
        "StatusCode": 200,
    }
    result = unwrap_envelope(payload, operation_name="ShipmentCommand/UpsertAdhocShipment")
    assert result == payload  # no "Data" key on a Command envelope — the body IS the result


def test_command_envelope_error_messages_raises_tool_error() -> None:
    payload: dict[str, Any] = {
        "RequestUri": None,
        "ExternalError": None,
        "HttpStatusCode": 400,
        "Errors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "ErrorMessages": ["Shipment is already discarded."],
        "StatusCode": 400,
    }
    with pytest.raises(ToolError, match="Shipment is already discarded"):
        unwrap_envelope(payload, operation_name="ShipmentCommand/DiscardShipment")


def test_command_envelope_external_error_raises_tool_error() -> None:
    payload: dict[str, Any] = {
        "RequestUri": None,
        "ExternalError": "Upstream KonsHub service timed out.",
        "HttpStatusCode": 502,
        "Errors": {"IsValid": True, "Errors": [], "RuleSetsExecuted": None},
        "ErrorMessages": [],
        "StatusCode": 502,
    }
    with pytest.raises(ToolError, match="Upstream KonsHub service timed out"):
        unwrap_envelope(payload, operation_name="MaterialHandoverCommand/CreateMaterialHandover")


# --------------------------------------------------------------------------
# The core invariant this API forces on every caller: HTTP 200 is not proof
# of success. `unwrap_envelope` only ever sees the parsed body — proving it
# raises here is what proves an HTTP-200-wrapped failure cannot slip through.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"IsSuccess": False, "ErrorMessage": "Business rule violated.", "Data": None},
        {"ErrorMessages": ["Business rule violated."], "ExternalError": None},
    ],
)
def test_http_200_body_can_still_signal_business_failure(payload: dict[str, Any]) -> None:
    """Simulates exactly what `api/client.py` will hand to `unwrap_envelope`:
    the JSON body of an HTTP 200 response whose *business* outcome failed.
    The HTTP status is deliberately absent from this call's inputs — this
    function must never need it to detect failure."""
    with pytest.raises(ToolError, match="Business rule violated"):
        unwrap_envelope(payload, operation_name="SomeOperation")


# --------------------------------------------------------------------------
# Non-dict bodies (e.g. a bare file/blob) pass through unchanged.
# --------------------------------------------------------------------------


def test_non_dict_payload_passes_through_unchanged() -> None:
    assert unwrap_envelope([1, 2, 3], operation_name="LeanCardQuery/DownloadSample") == [1, 2, 3]
    assert unwrap_envelope(None, operation_name="LeanCardQuery/DownloadSample") is None


# --------------------------------------------------------------------------
# extract_total_count
# --------------------------------------------------------------------------


def test_extract_total_count_reads_query_envelope_field() -> None:
    assert extract_total_count({"Data": [], "TotalCount": 143}) == 143


def test_extract_total_count_none_for_command_envelope() -> None:
    assert extract_total_count({"RequestUri": None, "ErrorMessages": []}) is None


def test_extract_total_count_none_for_non_dict_or_non_int() -> None:
    assert extract_total_count([1, 2, 3]) is None
    assert extract_total_count({"TotalCount": "not-an-int"}) is None
