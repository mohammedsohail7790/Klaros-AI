"""Marketplace lead-ingestion adapters (Angi, Thumbtack, Nextdoor).

HONESTY NOTE (read before touching this file): none of these three
providers publishes a public, self-serve webhook API or a documented
payload schema. Confirmed by research before writing this module — Angi's
own integration docs describe webhook delivery only through manual
partner onboarding (crmintegrations@angi.com) with an unpublished, "fixed"
JSON schema, commonly bridged via Zapier; Thumbtack and Nextdoor have no
public developer webhook documentation at all. Inventing a specific field
layout for any of them would be fabricating an external contract this
project's own rules forbid.

The honest, buildable thing is therefore NOT "parse Angi's JSON" — it's a
normalization boundary the TENANT configures once per marketplace, using
whatever bridge they actually have access to (the marketplace's own
webhook-config screen where one exists, e.g. Angi's, or a Zapier "Webhooks
by Zapier" action): a per-tenant shared secret (HMAC-signs the request,
verified the same way app/integrations/twilio_client.py verifies Twilio's
own signature, except this scheme is Klaros', not the marketplace's) plus
a per-tenant field map (dotted JSON paths -> Klaros' canonical lead
fields). Sensible generic defaults are provided per provider, based on
common lead-webhook JSON conventions, but they are DEFAULTS to override,
not a claim about any provider's real payload shape.

Credentials live in the existing per-tenant `IntegrationConnection`
mechanism (app/services/integration_connection_service.py) — same model
already used for QuickBooks/Google Calendar/Stripe. No verifier is
registered for these three providers (there is no live API to call), so
`connect()` honestly lands the connection in ERROR/"no verifier
registered", exactly like the existing Gmail/Google Ads/Meta Ads
precedent (see app/api/tool_deps_integrations.py) — the stored, encrypted
secret is still used by the webhook handler regardless of that status
field, which only ever reflects live-verification, never "is a secret
configured".
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NormalizedMarketplaceLead:
    external_lead_id: str
    name: str
    phone: str | None
    email: str | None
    location: str | None
    service_requested: str | None
    description: str | None


def _dig(payload: dict, dotted_path: str):
    node = payload
    for part in dotted_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class MarketplaceAdapter:
    provider_name: str
    #  canonical_field -> default dotted JSON path in an assumed-generic
    #  payload shape. Override per tenant via the connection's stored
    #  `field_map` credential key.
    default_field_map: dict[str, str] = {
        "external_lead_id": "id",
        "name": "name",
        "phone": "phone",
        "email": "email",
        "location": "location",
        "service_requested": "service",
        "description": "message",
    }

    def normalize_lead(self, payload: dict, *, field_map: dict[str, str] | None = None) -> NormalizedMarketplaceLead:
        fields = {**self.default_field_map, **(field_map or {})}
        external_lead_id = _dig(payload, fields["external_lead_id"])
        name = _dig(payload, fields["name"])
        if not external_lead_id or not name:
            raise ValueError(
                f"{self.provider_name} payload is missing a required field "
                f"(external_lead_id -> '{fields['external_lead_id']}', name -> '{fields['name']}') "
                "under the currently configured field_map"
            )
        return NormalizedMarketplaceLead(
            external_lead_id=str(external_lead_id),
            name=str(name),
            phone=_dig(payload, fields["phone"]),
            email=_dig(payload, fields["email"]),
            location=_dig(payload, fields["location"]),
            service_requested=_dig(payload, fields["service_requested"]),
            description=_dig(payload, fields["description"]),
        )


class AngiAdapter(MarketplaceAdapter):
    provider_name = "angi"


class ThumbtackAdapter(MarketplaceAdapter):
    provider_name = "thumbtack"
    default_field_map = {
        "external_lead_id": "id",
        "name": "customer.name",
        "phone": "customer.phone",
        "email": "customer.email",
        "location": "customer.zip_code",
        "service_requested": "category",
        "description": "details",
    }


class NextdoorAdapter(MarketplaceAdapter):
    provider_name = "nextdoor"
    default_field_map = {
        "external_lead_id": "lead_id",
        "name": "requester_name",
        "phone": "requester_phone",
        "email": "requester_email",
        "location": "neighborhood",
        "service_requested": "business_category",
        "description": "message",
    }


MARKETPLACE_ADAPTERS: dict[str, MarketplaceAdapter] = {
    "angi": AngiAdapter(),
    "thumbtack": ThumbtackAdapter(),
    "nextdoor": NextdoorAdapter(),
}
