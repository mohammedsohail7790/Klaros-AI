"""The business context Klaros hands to an AI workforce — assembled from Klaros' own
business data (blueprint, enabled industry module). It is a *preview* of what an adapter
would send; building it contacts nothing."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Generic defaults. An industry module extends these through its own registered
# workforce-context provider — this module never names an industry.
DEFAULT_QUALIFICATION_FIELDS = ("Service requested", "Location", "Timeline", "Contact details")
DEFAULT_ESCALATION_TRIGGERS = ("The customer asks for a person", "A complaint")
DEFAULT_BOOKING_RULES = ("Book only into your connected calendar", "Confirm with the customer before a booking is final")


@dataclass
class BusinessContextPack:
    business_name: str | None
    industry: str | None
    summary: str | None
    customers: str | None
    services: list[str] = field(default_factory=list)
    markets: list[str] = field(default_factory=list)
    qualification_fields: list[str] = field(default_factory=list)
    escalation_triggers: list[str] = field(default_factory=list)
    booking_rules: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "business_name": self.business_name, "industry": self.industry, "summary": self.summary,
            "customers": self.customers, "services": self.services, "markets": self.markets,
            "qualification_fields": self.qualification_fields,
            "escalation_triggers": self.escalation_triggers, "booking_rules": self.booking_rules,
        }


def _merge(*groups: Any) -> list[str]:
    out: list[str] = []
    for g in groups:
        for item in g or []:
            if item and item not in out:
                out.append(str(item))
    return out


def build_context_pack(business: dict[str, Any], contributions: list[dict[str, Any]]) -> BusinessContextPack:
    return BusinessContextPack(
        business_name=business.get("name"),
        industry=business.get("industry"),
        summary=business.get("summary"),
        customers=business.get("customers"),
        services=_merge(*(c.get("services") for c in contributions)),
        markets=_merge(*(c.get("markets") for c in contributions)),
        qualification_fields=_merge(DEFAULT_QUALIFICATION_FIELDS, *(c.get("qualification_fields") for c in contributions)),
        escalation_triggers=_merge(DEFAULT_ESCALATION_TRIGGERS, *(c.get("escalation_triggers") for c in contributions)),
        booking_rules=_merge(DEFAULT_BOOKING_RULES, *(c.get("booking_rules") for c in contributions)),
    )
