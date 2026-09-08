"""Unit tests for Settings: per-service base URL resolution and the
CLM_WRITE_TOOLS opt-in parsing."""

from __future__ import annotations

import pytest

from clm_mcp.config import Settings


def test_base_url_defaults_derive_from_api_root_url() -> None:
    settings = Settings()
    assert (
        settings.base_url_for("shipment")
        == "https://msblocks.selisestage.com/api/business-clm-shipment"
    )
    assert (
        settings.base_url_for("construction")
        == "https://msblocks.selisestage.com/api/business-clm-construction"
    )
    assert settings.base_url_for("team") == "https://msblocks.selisestage.com/api/business-clm-team"
    assert (
        settings.base_url_for("konshub")
        == "https://msblocks.selisestage.com/api/business-clm-konshub"
    )


def test_deprecated_api_base_url_overrides_only_shipment() -> None:
    settings = Settings(api_base_url="https://example.test/shipment-override")
    assert settings.base_url_for("shipment") == "https://example.test/shipment-override"
    # Every other service is unaffected by the deprecated shipment-only field.
    assert (
        settings.base_url_for("konshub")
        == "https://msblocks.selisestage.com/api/business-clm-konshub"
    )


def test_per_service_override_takes_precedence_over_deprecated_field() -> None:
    settings = Settings(
        api_base_url="https://example.test/shipment-override",
        api_base_url_overrides={"shipment": "https://example.test/wins"},
    )
    assert settings.base_url_for("shipment") == "https://example.test/wins"


def test_per_service_override_applies_to_any_service() -> None:
    settings = Settings(api_base_url_overrides={"konshub": "https://example.test/konshub-override"})
    assert settings.base_url_for("konshub") == "https://example.test/konshub-override"
    assert settings.base_url_for("team") == "https://msblocks.selisestage.com/api/business-clm-team"


def test_base_url_for_unknown_service_raises() -> None:
    settings = Settings()
    with pytest.raises(ValueError, match="Unknown CLM service"):
        settings.base_url_for("not-a-real-service")


def test_write_tool_services_defaults_to_empty() -> None:
    assert Settings().write_tool_services() == frozenset()


def test_write_tool_services_parses_comma_separated_slugs() -> None:
    settings = Settings(write_tools="shipment, konshub")
    assert settings.write_tool_services() == {"shipment", "konshub"}


def test_write_tool_services_all_means_every_service() -> None:
    settings = Settings(write_tools="all")
    assert settings.write_tool_services() == {"shipment", "construction", "team", "konshub"}


def test_write_tool_services_case_insensitive_all() -> None:
    assert Settings(write_tools="ALL").write_tool_services() == {
        "shipment",
        "construction",
        "team",
        "konshub",
    }


def test_write_tool_services_rejects_unknown_slug() -> None:
    settings = Settings(write_tools="shipment,not-a-service")
    with pytest.raises(ValueError, match="unknown service"):
        settings.write_tool_services()
