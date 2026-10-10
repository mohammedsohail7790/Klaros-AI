"""Consent enforcement for tenants whose business requires explicit consent before personal or health data is kept.

WHO is gated: a tenant is "consent-gated" when its Halla connection carries a safety profile with `requires_consent_evidence`, OR when it has a
vertical enabled whose key is listed in GATED_VERTICAL_KEYS (so a tenant cannot escape the gate by simply not having a Halla connection).
Everyone else is untouched: every function here is a no-op for them.

WHAT is enforced (service layer, so routes, agent tools, imports, public forms and Halla events all pass through it):
  * `store_personal_data`        required to create a lead (name / phone / email)
  * `store_medical_information`  required to keep treatment or free-text health detail (`service_requested`, `description`, health intake fields)
  * `contact`                    required before outbound contact (Halla call / sync)
Claims are made by a PERSON (operator attestation, with their user id) or by the lead themself (public form checkboxes) or come from Halla's own
signed evidence. An AI agent / workflow / system actor can never attest consent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Iterable

from app.models.actor import ActorType

STORE_PERSONAL, STORE_MEDICAL, CONTACT = "store_personal_data", "store_medical_information", "contact"
ALL_SCOPES = (CONTACT, STORE_PERSONAL, STORE_MEDICAL)
GATED_VERTICAL_KEYS = ("medical_tourism",)

SOURCE_HALLA, SOURCE_WEB, SOURCE_OPERATOR = "halla", "web_form", "operator_attested"


class ConsentRequiredError(Exception):
    """Raised before anything is persisted. `missing` names scopes only; `message` never includes the data that was refused."""

    def __init__(self, missing: Iterable[str], reason: str = "consent_required") -> None:
        self.missing = sorted(set(missing))
        self.reason = reason
        super().__init__(f"{reason}: {', '.join(self.missing)}" if self.missing else reason)


@dataclass(frozen=True)
class ConsentClaim:
    """What the caller asserts the person agreed to. `recorded=True` means the evidence is already in the history (Halla path)."""

    scopes: frozenset[str]
    source: str
    wording_version: str | None = None
    actor_user_id: uuid.UUID | None = None
    recorded: bool = False


def claim_from_tool(raw: dict | None, actor_type: ActorType, actor_id: uuid.UUID | None) -> ConsentClaim | None:
    """A claim supplied through the tool/API layer. Only a signed-in PERSON may attest; anything else yields no claim (=> refused)."""
    if not raw or actor_type != ActorType.USER or actor_id is None:
        return None
    scopes = frozenset(s for s in (raw.get("scopes") or []) if s in ALL_SCOPES)
    return ConsentClaim(scopes, SOURCE_OPERATOR, (raw.get("wording_version") or None), actor_id) if scopes else None


async def tenant_requires_consent(session_factory, tenant_id: uuid.UUID | None) -> bool:
    if tenant_id is None:
        return False
    from app.db.session import set_tenant_context
    from app.services import pilot_safety
    from app.services.vertical_extension_service import VerticalExtensionService

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        profile = await pilot_safety.profile_in_session(session, tenant_id)
    if profile is not None and profile.requires_consent_evidence:
        return True
    service = VerticalExtensionService(session_factory)
    for key in GATED_VERTICAL_KEYS:
        try:
            if await service.is_enabled_for_organization(tenant_id, key):
                return True
        except Exception:  # noqa: BLE001 - a catalogue lookup problem must not break a non-gated tenant's intake
            continue
    return False


async def approved_wording_versions(session_factory, tenant_id: uuid.UUID) -> list[str]:
    """Wording labels the OWNER approved for public forms (connection metadata `approved_wording_versions`). Empty => public intake stays closed."""
    from app.db.session import set_tenant_context
    from app.models.integration import IntegrationConnection
    from sqlalchemy import select

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        conn = (await session.execute(select(IntegrationConnection).where(IntegrationConnection.tenant_id == tenant_id, IntegrationConnection.provider == "halla"))).scalar_one_or_none()
    meta = conn.connection_metadata if conn is not None and isinstance(conn.connection_metadata, dict) else {}
    versions = meta.get("approved_wording_versions")
    return [v for v in versions if isinstance(v, str) and v] if isinstance(versions, list) else []


def check_create(claim: ConsentClaim | None, *, has_medical_fields: bool) -> None:
    """Raise ConsentRequiredError unless the claim permits storing this new record."""
    need = [STORE_PERSONAL] + ([STORE_MEDICAL] if has_medical_fields else [])
    have = claim.scopes if claim is not None else frozenset()
    missing = [s for s in need if s not in have]
    if missing:
        raise ConsentRequiredError(missing)
