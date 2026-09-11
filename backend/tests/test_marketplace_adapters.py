"""Unit tests for the marketplace lead-normalization adapters themselves
(app/integrations/marketplace_adapters.py), independent of the webhook
transport layer."""

import pytest

from app.integrations.marketplace_adapters import (
    AngiAdapter,
    MARKETPLACE_ADAPTERS,
    NextdoorAdapter,
    ThumbtackAdapter,
)


def test_angi_default_field_map_normalizes_a_flat_payload() -> None:
    result = AngiAdapter().normalize_lead(
        {"id": "a1", "name": "Jane Doe", "phone": "555-1111", "email": "jane@example.com",
         "location": "Austin, TX", "service": "Plumbing", "message": "Leaky faucet"}
    )
    assert result.external_lead_id == "a1"
    assert result.name == "Jane Doe"
    assert result.service_requested == "Plumbing"


def test_thumbtack_default_field_map_normalizes_a_nested_payload() -> None:
    result = ThumbtackAdapter().normalize_lead(
        {"id": "t1", "customer": {"name": "John Smith", "phone": "555-2222", "email": "john@example.com", "zip_code": "78701"},
         "category": "Electrical", "details": "Outlet install"}
    )
    assert result.name == "John Smith"
    assert result.location == "78701"


def test_nextdoor_default_field_map_normalizes_its_own_shape() -> None:
    result = NextdoorAdapter().normalize_lead(
        {"lead_id": "n1", "requester_name": "Alex Rivera", "requester_phone": "555-3333",
         "requester_email": "alex@example.com", "neighborhood": "Downtown", "business_category": "Landscaping",
         "message": "Need lawn care"}
    )
    assert result.external_lead_id == "n1"
    assert result.name == "Alex Rivera"


def test_missing_required_field_raises_value_error() -> None:
    with pytest.raises(ValueError):
        AngiAdapter().normalize_lead({"id": "a2"})  # no name


def test_custom_field_map_fully_overrides_defaults() -> None:
    result = AngiAdapter().normalize_lead(
        {"ref": "custom-1", "who": {"name": "Custom Name"}},
        field_map={"external_lead_id": "ref", "name": "who.name"},
    )
    assert result.external_lead_id == "custom-1"
    assert result.name == "Custom Name"
    # Fields not overridden fall back to the adapter's own defaults, which
    # won't resolve against this payload shape — must be None, never crash.
    assert result.phone is None


def test_registry_contains_exactly_the_three_target_marketplaces() -> None:
    assert set(MARKETPLACE_ADAPTERS.keys()) == {"angi", "thumbtack", "nextdoor"}
    for provider, adapter in MARKETPLACE_ADAPTERS.items():
        assert adapter.provider_name == provider
