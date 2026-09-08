"""Unit tests for OperationRegistry: multi-service merge, exclusion, $ref
resolution, cycle-breaking, and canonical/unqualified name resolution."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from clm_mcp.spec.registry import (
    SPEC_DIR,
    AmbiguousOperationError,
    OperationRegistry,
    get_registry,
)

_SLUGS = ("shipment", "construction", "team", "konshub")


@pytest.fixture(scope="module")
def raw_specs() -> dict[str, dict[str, Any]]:
    return {
        slug: json.loads((SPEC_DIR / f"{slug}.json").read_text(encoding="utf-8")) for slug in _SLUGS
    }


def test_test_tag_operations_are_excluded_in_every_service(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    """GetUserData (and the rest of the Test/* tag) leaks super-admin
    credentials upstream in all four services — see PLAN.md. It must never
    be reachable, in any of them."""
    registry = OperationRegistry(raw_specs)
    assert not any(o.tag == "Test" for o in registry.list_operations())
    for slug in _SLUGS:
        assert registry.get(f"{slug}/Test/GetUserData") is None
    # Excluded from every service, so the unqualified form has no entries in
    # the unqualified index at all — resolves to None, not ambiguous.
    assert registry.resolve("Test/GetUserData") is None


def test_get_shipment_by_id_resolves_expected_shape(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    registry = OperationRegistry(raw_specs)
    op = registry.get("shipment/ShipmentQuery/GetShipmentById")
    assert op is not None
    assert op.service == "shipment"
    assert op.method == "post"
    assert op.is_query
    assert not op.is_command
    assert op.request_schema is not None
    assert set(op.request_schema["properties"]) == {"ShipmentId"}


def test_get_operation_without_request_body_has_none_schema(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    registry = OperationRegistry(raw_specs)
    op = registry.get("shipment/ExternalQuery/GetShipment")
    assert op is not None
    assert op.method == "get"
    assert op.request_schema is None
    assert [p["name"] for p in op.parameters] == ["shipmentId"]


def test_no_duplicate_operation_names(raw_specs: dict[str, dict[str, Any]]) -> None:
    registry = OperationRegistry(raw_specs)
    names = [o.name for o in registry.list_operations()]
    assert len(names) == len(set(names))


def test_operation_count_per_service(raw_specs: dict[str, dict[str, Any]]) -> None:
    """Pinned during design (PLAN.md "Verified facts") — a mismatch means a
    spec changed; re-run scripts/refresh_spec.py and review before updating."""
    registry = OperationRegistry(raw_specs)
    counts = {slug: len(registry.list_operations(service=slug)) for slug in _SLUGS}
    assert counts == {"shipment": 121, "construction": 96, "team": 61, "konshub": 86}
    assert len(registry) == 364


def test_list_operations_filters_by_service_tag_and_search(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    registry = OperationRegistry(raw_specs)
    incident_commands = registry.list_operations(tag="IncidentCommand")
    assert {o.name for o in incident_commands} == {
        "shipment/IncidentCommand/CreateIncident",
        "shipment/IncidentCommand/DeleteIncident",
        "shipment/IncidentCommand/UpdateIncident",
    }

    by_search = registry.list_operations(search="getshipmentbyid")
    assert [o.name for o in by_search] == ["shipment/ShipmentQuery/GetShipmentById"]

    konshub_only = registry.list_operations(service="konshub")
    assert konshub_only
    assert all(o.service == "konshub" for o in konshub_only)


def test_resolve_accepts_unqualified_name_when_unique(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    """Backwards compatibility: every pre-multi-service caller (curated
    shipment service modules, README/USAGE examples) uses the unqualified
    `{Tag}/{PathTail}` form."""
    registry = OperationRegistry(raw_specs)
    op = registry.resolve("ShipmentQuery/GetShipmentById")
    assert op is not None
    assert op.name == "shipment/ShipmentQuery/GetShipmentById"

    assert registry.resolve("ShipmentQuery/DoesNotExist") is None


def test_resolve_raises_on_genuine_ambiguity(raw_specs: dict[str, dict[str, Any]]) -> None:
    """Construct an artificial collision (none exist among real operations
    today — see PLAN.md) and confirm resolve() refuses to silently pick one."""
    specs = copy.deepcopy(raw_specs)
    # Give shipment and konshub the same Tag/PathTail under a fresh, unused tag.
    shared_op = {
        "tags": ["SharedTestTag"],
        "responses": {"200": {"description": "ok"}},
    }
    specs["shipment"]["paths"]["/ClmShipmentWebService/SharedTestTag/DoAmbiguousThing"] = {
        "post": shared_op
    }
    specs["konshub"]["paths"]["/ClmKonshubWebService/SharedTestTag/DoAmbiguousThing"] = {
        "post": shared_op
    }
    registry = OperationRegistry(specs)
    with pytest.raises(AmbiguousOperationError) as exc_info:
        registry.resolve("SharedTestTag/DoAmbiguousThing")
    assert "shipment/SharedTestTag/DoAmbiguousThing" in exc_info.value.candidates
    assert "konshub/SharedTestTag/DoAmbiguousThing" in exc_info.value.candidates


def test_cyclic_schema_is_broken_with_a_placeholder_not_raised(
    raw_specs: dict[str, dict[str, Any]],
) -> None:
    """A $ref cycle (real ones exist in the construction and konshub specs —
    see PLAN.md) must not fail the whole registry build; it resolves to a
    permissive object placeholder at the point the cycle closes."""
    specs = copy.deepcopy(raw_specs)
    specs["shipment"]["components"]["schemas"]["CycleA"] = {
        "type": "object",
        "properties": {"b": {"$ref": "#/components/schemas/CycleB"}},
    }
    specs["shipment"]["components"]["schemas"]["CycleB"] = {
        "type": "object",
        "properties": {"a": {"$ref": "#/components/schemas/CycleA"}},
    }
    specs["shipment"]["paths"]["/ClmShipmentWebService/ShipmentQuery/GetShipmentById"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"] = {"$ref": "#/components/schemas/CycleA"}

    registry = OperationRegistry(specs)
    op = registry.get("shipment/ShipmentQuery/GetShipmentById")
    assert op is not None
    assert op.request_schema is not None
    # CycleA -> CycleB -> CycleA: the second visit to CycleA is the break point.
    inner = op.request_schema["properties"]["b"]["properties"]["a"]
    assert inner["type"] == "object"
    assert inner["title"] == "CycleA"
    assert "recursive reference" in inner["description"]


def test_real_cyclic_operations_are_describable(raw_specs: dict[str, dict[str, Any]]) -> None:
    """The real cycles found during design (PLAN.md) must not prevent the
    registry from building, and each affected operation must resolve with a
    valid (if permissive) schema."""
    registry = OperationRegistry(raw_specs)

    split = registry.get("konshub/KonsHubShipmentCommand/SplitKonsHubShipment")
    assert split is not None
    assert split.request_schema is not None

    deliveries = registry.get("konshub/KonsHubShipmentQuery/GetDeliveriesTabShipmentList")
    assert deliveries is not None
    assert deliveries.response_schema is not None

    mobile = registry.get("konshub/KonsHubShipmentQuery/GetShipmentDetailsForMobile")
    assert mobile is not None

    reservation = registry.get(
        "construction/ConstructionManagementQuery/GetSiteOverviewAndUPReservation"
    )
    assert reservation is not None
    assert reservation.response_schema is not None


def test_path_prefix_mismatch_raises(raw_specs: dict[str, dict[str, Any]]) -> None:
    """A future spec/catalog drift (services_catalog.py's path_prefix no
    longer matching the spec's actual paths) must fail loudly at build time."""
    specs = copy.deepcopy(raw_specs)
    specs["shipment"]["paths"]["/SomeOtherPrefix/ShipmentQuery/Bogus"] = specs["shipment"][
        "paths"
    ].pop("/ClmShipmentWebService/ExternalQuery/GetShipment")
    with pytest.raises(ValueError, match="does not start with the expected prefix"):
        OperationRegistry(specs)


def test_get_registry_is_cached_singleton() -> None:
    assert get_registry() is get_registry()


def test_get_registry_loads_all_four_services() -> None:
    registry = get_registry()
    assert len(registry) == 364
    assert {o.service for o in registry.list_operations()} == {
        "shipment",
        "construction",
        "team",
        "konshub",
    }


def test_vendored_spec_files_exist() -> None:
    for slug in _SLUGS:
        assert (SPEC_DIR / f"{slug}.json").is_file()
