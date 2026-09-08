"""Unit tests for the CLM service catalog."""

from __future__ import annotations

from clm_mcp.services_catalog import DEFAULT_SERVICE, SERVICES, ClmService


def test_four_services_registered() -> None:
    assert set(SERVICES) == {"shipment", "construction", "team", "konshub"}


def test_default_service_is_shipment() -> None:
    assert DEFAULT_SERVICE == "shipment"
    assert DEFAULT_SERVICE in SERVICES


def test_each_service_slug_matches_its_dict_key() -> None:
    for slug, service in SERVICES.items():
        assert isinstance(service, ClmService)
        assert service.slug == slug


def test_gateway_segments_follow_business_clm_convention() -> None:
    for slug, service in SERVICES.items():
        assert service.gateway_segment == f"business-clm-{slug}"


def test_path_prefixes_are_distinct_and_match_title() -> None:
    prefixes = [s.path_prefix for s in SERVICES.values()]
    assert len(prefixes) == len(set(prefixes)), "path prefixes must be unique across services"
    for service in SERVICES.values():
        assert service.path_prefix == f"/{service.title}"


def test_spec_filenames_are_distinct() -> None:
    filenames = [s.spec_filename for s in SERVICES.values()]
    assert len(filenames) == len(set(filenames))
