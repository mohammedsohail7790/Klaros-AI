"""The shared capability vocabulary: free-text normalisation and the
genericity guarantee for the Business Builder modules."""

from pathlib import Path

import pytest

from app.services.capability_vocabulary import CAPABILITIES, canonical_key, describe, split_capability_text

_APP = Path(__file__).resolve().parent.parent / "app"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("payments", "payment_processing"),
        ("Payment Processing", "payment_processing"),
        ("a CRM", "crm"),
        ("Consultation scheduling", "appointment_scheduling"),
        ("WhatsApp and phone communication", "communication"),
        ("hospital and doctor directory", "provider_directory"),
        ("email marketing", "marketing"),  # longer alias wins over "email"
        ("some_vertical.patient_leads", "lead_capture"),  # namespaced registry capability
        ("workshop", "workshop"),  # single-word aliases only match whole words
        ("A Totally New Thing", "a_totally_new_thing"),  # unknown -> stable snake_case, never dropped
    ],
)
def test_canonical_key(phrase: str, expected: str) -> None:
    assert canonical_key(phrase) == expected


def test_split_never_fragments_a_single_phrase() -> None:
    assert split_capability_text("lead capture") == ["lead capture"]
    assert split_capability_text("bookings, payments and inventory") == ["bookings", "payments", "inventory"]
    assert split_capability_text("  ") == []


def test_unknown_capability_is_planned_never_supported() -> None:
    d = describe("quantum_widgets")
    assert d.klaros_support == "PLANNED" and d.group == "Other" and d.native_route is None


def test_every_dependency_refers_to_a_real_capability() -> None:
    for d in CAPABILITIES.values():
        for dep in d.depends_on:
            assert dep in CAPABILITIES, (d.key, dep)


def test_no_alias_is_ambiguous_between_two_capabilities() -> None:
    seen: dict[str, str] = {}
    for d in CAPABILITIES.values():
        for a in d.aliases:
            norm = " ".join(a.lower().replace("-", " ").split())
            assert seen.setdefault(norm, d.key) == d.key, f"alias {a!r} claimed by {seen[norm]} and {d.key}"


@pytest.mark.parametrize(
    "relpath",
    [
        "services/business_builder_service.py",
        "services/capability_vocabulary.py",
        "api/v1/business_builder.py",
        "services/business_operations_service.py",
        "services/operations_providers.py",
        "services/business_workforce_service.py",
        "integrations/workforce/context.py",
        "integrations/workforce/events.py",
        "integrations/workforce/dev_adapter.py",
        "integrations/workforce/contract.py",
        "integrations/workforce/registry.py",
    ],
)
def test_business_builder_modules_never_name_a_vertical(relpath: str) -> None:
    text = (_APP / relpath).read_text()
    for forbidden in ("medical_tourism", "dropshipping", "Medical Tourism", "Dropshipping"):
        assert forbidden not in text, f"{relpath} names a vertical ({forbidden!r}); vertical behaviour must be data"


def test_a_flag_claim_naming_two_capabilities_yields_both_and_a_single_key_is_kept_verbatim() -> None:
    from types import SimpleNamespace

    from app.services.recommendation_service import _capability_keys_from_claim as keys

    both = SimpleNamespace(value=True, key="inventory and pricing")
    assert keys(both) == ["inventory", "pricing"]
    single = SimpleNamespace(value=True, key="customer_management")
    assert keys(single) == ["customer_management"]  # structured keys are never rewritten
    assert keys(SimpleNamespace(value=["Payments"], key="x")) == ["payments"]
