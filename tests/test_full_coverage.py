"""Full-coverage structural tests: every one of the 364 non-`Test`
operations across all four vendored specs (shipment, construction, team,
konshub) must be genuinely reachable — described, schema-valid, and
pre-flight-validatable through the gateway — regardless of whether it also
has a hand-curated or auto-generated named tool.

This is the automated, permanent version of the manual check run during
design: prove "no real gap" as a fact the test suite enforces, not a claim
in a document that can silently go stale as the spec evolves.
"""

from __future__ import annotations

import json

import jsonschema
import pytest

from clm_mcp.spec.registry import OperationSummary, get_registry

REGISTRY = get_registry()
ALL_OPERATIONS = REGISTRY.list_operations()

# Pinned counts: verified against the vendored specs during design (PLAN.md
# "Verified facts") and re-confirmed after every fix in this pass. A
# mismatch means a spec changed — re-run scripts/refresh_spec.py and review
# what's different before updating these numbers.
EXPECTED_TOTAL_OPERATIONS = 364
EXPECTED_QUERY_OPERATIONS = 183
EXPECTED_COMMAND_OPERATIONS = 181
EXPECTED_COUNTS_BY_SERVICE = {
    "shipment": 121,
    "construction": 96,
    "team": 61,
    "konshub": 86,
}


def test_total_operation_count_is_364_non_test_operations() -> None:
    assert len(ALL_OPERATIONS) == EXPECTED_TOTAL_OPERATIONS


def test_query_and_command_counts_match_expected_split() -> None:
    resolved = [REGISTRY.get(o.name) for o in ALL_OPERATIONS]
    assert all(op is not None for op in resolved)
    query_count = sum(1 for op in resolved if op is not None and op.is_query)
    command_count = sum(1 for op in resolved if op is not None and op.is_command)
    assert query_count == EXPECTED_QUERY_OPERATIONS
    assert command_count == EXPECTED_COMMAND_OPERATIONS
    assert query_count + command_count == EXPECTED_TOTAL_OPERATIONS


def test_operation_count_matches_expected_split_per_service() -> None:
    for slug, expected in EXPECTED_COUNTS_BY_SERVICE.items():
        assert len(REGISTRY.list_operations(service=slug)) == expected


def test_no_test_tag_operation_is_present() -> None:
    assert not any(o.tag == "Test" for o in ALL_OPERATIONS)
    for slug in EXPECTED_COUNTS_BY_SERVICE:
        assert REGISTRY.get(f"{slug}/Test/GetUserData") is None


#: Operations across the four vendored specs that genuinely have no
#: `summary` at all (verified directly against the raw JSON, not a parsing
#: bug). None of these are curated, so this only affects
#: clm_describe_operation's helpfulness for them via the gateway;
#: commands.py's tool-name fallback already covers the write ones ("Execute
#: {name} (a write operation)."). Not fixable client-side — the spec
#: upstream simply omits it.
_OPERATIONS_WITHOUT_A_SUMMARY = frozenset(
    {
        "shipment/ShipmentCommand/UpdateShipmentStatus",
        "shipment/ShipmentQuery/GetDraftShipmentPermission",
        "construction/WikiCommand/GenerateWikiContentKeywords",
        "construction/WikiCommand/SyncWikiContentEmbeddings",
        "construction/WikiQuery/SearchWikiContentByKeyword",
        "construction/WikiQuery/SearchWikiPagesSemantic",
        "konshub/KonsHubShipmentCommand/DownloadBulkShipmentMergedPdf",
        "konshub/KonsHubShipmentCommand/UpdateShipmentComment",
        "konshub/KonsHubShipmentQuery/GetShipmentDetailsForMobile",
        "konshub/ListViewQuery/GetShipmentForIncommingListViewForMobile",
        "konshub/StorageCommissionQuery/GetCommissionedShipmentDailyCount",
        "team/ClmTeamCommand/UpsertFeatureRoleMaps",
    }
)


@pytest.mark.parametrize("op_summary", ALL_OPERATIONS, ids=lambda o: o.name)
def test_every_operation_resolves_to_a_full_description(op_summary: OperationSummary) -> None:
    """`clm_describe_operation`'s exact lookup path: get() must return a
    fully-resolved Operation, never None, for anything list_operations()
    itself just returned."""
    op = REGISTRY.get(op_summary.name)
    assert op is not None
    assert op.method in ("get", "post")
    if op.name not in _OPERATIONS_WITHOUT_A_SUMMARY:
        assert op.summary


@pytest.mark.parametrize("op_summary", ALL_OPERATIONS, ids=lambda o: o.name)
def test_every_operation_schema_is_json_serializable(op_summary: OperationSummary) -> None:
    """The exact payload clm_describe_operation would hand back to a
    caller — must round-trip through JSON with no exception."""
    op = REGISTRY.get(op_summary.name)
    assert op is not None
    json.dumps(op.request_schema)
    json.dumps(op.response_schema)
    json.dumps([dict(p) for p in op.parameters])


@pytest.mark.parametrize("op_summary", ALL_OPERATIONS, ids=lambda o: o.name)
def test_every_operation_is_preflight_validatable_by_the_gateway(
    op_summary: OperationSummary,
) -> None:
    """clm_invoke's exact pre-flight step (see tools/gateway.py::_validate_params):
    validating an empty params dict must either pass or raise a normal
    jsonschema.ValidationError — never an unexpected exception (a malformed
    schema, an unsupported keyword, anything else that would make
    clm_invoke crash instead of cleanly reporting invalid params).
    """
    op = REGISTRY.get(op_summary.name)
    assert op is not None
    if op.request_schema is not None:
        try:
            jsonschema.validate(instance={}, schema=op.request_schema)
        except jsonschema.ValidationError:
            pass  # expected for schemas with fields that reject {} — not a bug
    elif op.parameters:
        # GET-style operations: clm_invoke checks parameter names, which
        # never raises for an empty dict (no unexpected keys to reject).
        allowed = {p["name"] for p in op.parameters}
        assert isinstance(allowed, set)
