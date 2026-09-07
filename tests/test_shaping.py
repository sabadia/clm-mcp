"""Unit tests for spec/shaping.py: null-stripping, field projection, and
byte-cap truncation of list responses."""

from __future__ import annotations

from typing import Any

from clm_mcp.spec.shaping import project_fields, shape_list_response, strip_nulls

# --------------------------------------------------------------------------
# strip_nulls
# --------------------------------------------------------------------------


def test_strip_nulls_removes_none_valued_dict_keys() -> None:
    value = {"Id": "1", "Comment": None, "Nested": {"A": None, "B": 2}}
    assert strip_nulls(value) == {"Id": "1", "Nested": {"B": 2}}


def test_strip_nulls_keeps_falsy_non_none_values() -> None:
    value = {"Count": 0, "Flag": False, "Name": "", "Items": []}
    assert strip_nulls(value) == value


def test_strip_nulls_preserves_list_length_and_order_including_none_elements() -> None:
    value = [1, None, {"A": None, "B": 2}, 3]
    result = strip_nulls(value)
    assert len(result) == 4
    assert result[0] == 1
    assert result[1] is None  # list elements are never dropped, only recursed into
    assert result[2] == {"B": 2}
    assert result[3] == 3


# --------------------------------------------------------------------------
# project_fields
# --------------------------------------------------------------------------


def test_project_fields_keeps_only_requested_keys_on_each_item() -> None:
    data = [{"Id": "1", "Name": "A", "Extra": "x"}, {"Id": "2", "Name": "B", "Extra": "y"}]
    result = project_fields(data, ["Id", "Name"])
    assert result == [{"Id": "1", "Name": "A"}, {"Id": "2", "Name": "B"}]


def test_project_fields_noop_when_fields_falsy() -> None:
    data = [{"Id": "1", "Name": "A"}]
    assert project_fields(data, None) is data
    assert project_fields(data, []) is data


def test_project_fields_handles_single_dict_not_just_lists() -> None:
    assert project_fields({"Id": "1", "Name": "A"}, ["Id"]) == {"Id": "1"}


# --------------------------------------------------------------------------
# shape_list_response
# --------------------------------------------------------------------------


def test_shape_list_response_under_budget_returns_everything_untruncated() -> None:
    data: list[dict[str, Any]] = [{"Id": "1", "Comment": None}, {"Id": "2", "Comment": "hi"}]
    result = shape_list_response(data, total_count=2)

    assert result.data == [{"Id": "1"}, {"Id": "2", "Comment": "hi"}]
    assert result.total_count == 2
    assert result.returned == 2
    assert result.truncated is False
    assert result.note is None


def test_shape_list_response_applies_field_projection() -> None:
    data = [{"Id": "1", "Name": "A", "Extra": "drop me"}]
    result = shape_list_response(data, fields=["Id", "Name"])
    assert result.data == [{"Id": "1", "Name": "A"}]


def test_shape_list_response_truncates_when_over_byte_budget() -> None:
    # Each row is a fixed, known size once serialized; pick max_bytes to
    # allow roughly half the rows through, and assert the truncation note
    # explains what happened rather than silently dropping rows.
    data = [{"Id": str(i), "Payload": "x" * 100} for i in range(50)]
    result = shape_list_response(data, total_count=50, max_bytes=2000)

    assert result.truncated is True
    assert 0 < result.returned < 50
    assert result.data == data[: result.returned]  # truncated from the end, rows kept whole
    assert result.note is not None
    assert str(result.returned) in result.note
    assert "50" in result.note


def test_shape_list_response_handles_none_data() -> None:
    result = shape_list_response(None, total_count=0)
    assert result.data == []
    assert result.returned == 0
    assert result.truncated is False


def test_shape_list_response_single_row_over_budget_returns_empty_with_note() -> None:
    data = [{"Id": "1", "Payload": "x" * 10_000}]
    result = shape_list_response(data, max_bytes=100)

    assert result.truncated is True
    assert result.returned == 0
    assert result.data == []
    assert result.note is not None
    assert "none could be returned" in result.note
