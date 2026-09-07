"""Unit tests for OperationRegistry: exclusion, $ref resolution, cycle detection."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from clm_mcp.spec.registry import SPEC_PATH, OperationRegistry, SchemaCycleError, get_registry


@pytest.fixture(scope="module")
def raw_spec() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
    return loaded


def test_test_tag_operations_are_excluded(raw_spec: dict[str, Any]) -> None:
    """GetUserData (and the rest of the Test/* tag) leaks super-admin
    credentials upstream — see PLAN.md. It must never be reachable."""
    registry = OperationRegistry(raw_spec)
    assert "Test" not in registry.tags()
    assert registry.get("Test/GetUserData") is None
    assert not any(o.name.startswith("Test/") for o in registry.list_operations())


def test_get_shipment_by_id_resolves_expected_shape(raw_spec: dict[str, Any]) -> None:
    registry = OperationRegistry(raw_spec)
    op = registry.get("ShipmentQuery/GetShipmentById")
    assert op is not None
    assert op.method == "post"
    assert op.is_query
    assert not op.is_command
    assert op.request_schema is not None
    assert set(op.request_schema["properties"]) == {"ShipmentId"}


def test_get_operation_without_request_body_has_none_schema(raw_spec: dict[str, Any]) -> None:
    registry = OperationRegistry(raw_spec)
    op = registry.get("ExternalQuery/GetShipment")
    assert op is not None
    assert op.method == "get"
    assert op.request_schema is None
    assert [p["name"] for p in op.parameters] == ["shipmentId"]


def test_no_duplicate_operation_names(raw_spec: dict[str, Any]) -> None:
    registry = OperationRegistry(raw_spec)
    names = [o.name for o in registry.list_operations()]
    assert len(names) == len(set(names))


def test_list_operations_filters_by_tag_and_search(raw_spec: dict[str, Any]) -> None:
    registry = OperationRegistry(raw_spec)
    incident_commands = registry.list_operations(tag="IncidentCommand")
    assert {o.name for o in incident_commands} == {
        "IncidentCommand/CreateIncident",
        "IncidentCommand/DeleteIncident",
        "IncidentCommand/UpdateIncident",
    }

    by_search = registry.list_operations(search="getshipmentbyid")
    assert [o.name for o in by_search] == ["ShipmentQuery/GetShipmentById"]


def test_cyclic_schema_raises(raw_spec: dict[str, Any]) -> None:
    """A future spec change that introduces a $ref cycle must fail loudly
    at registry-build time, not silently produce a broken tool schema."""
    spec = copy.deepcopy(raw_spec)
    spec["components"]["schemas"]["CycleA"] = {
        "type": "object",
        "properties": {"b": {"$ref": "#/components/schemas/CycleB"}},
    }
    spec["components"]["schemas"]["CycleB"] = {
        "type": "object",
        "properties": {"a": {"$ref": "#/components/schemas/CycleA"}},
    }
    spec["paths"]["/ClmShipmentWebService/ShipmentQuery/GetShipmentById"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"] = {"$ref": "#/components/schemas/CycleA"}

    with pytest.raises(SchemaCycleError):
        OperationRegistry(spec)


def test_get_registry_is_cached_singleton() -> None:
    assert get_registry() is get_registry()


def test_vendored_spec_path_exists() -> None:
    assert Path(SPEC_PATH).is_file()
