"""Xero -> Klaros contact import (pull only). Imports a tenant's existing Xero contacts as Klaros customers, idempotently:

  * a Customer already linked (external_provider="xero", external_id=<ContactID>) is left alone (`already_linked`),
  * otherwise an exact normalized email/phone match (customer_matching, never fuzzy) links the existing customer to the Xero id (`matched`) and creates nothing,
  * otherwise a new Customer is created (`created`).
Supplier-only contacts and contacts without a name are skipped (`skipped`). Everything is scoped to one tenant (tenant context + tenant_id filters), and consent-gated tenants
are refused like every other customer import.

Xero refresh tokens rotate, so whenever this service refreshes it persists the new credential before making any other call.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.integrations import xero_client as xc
from app.integrations.credential_store import decrypt_credential
from app.integrations.xero_client import XeroAPIError, XeroClient, XeroErrorType
from app.models.crm import Customer
from app.models.integration import ConnectionStatus
from app.services.customer_matching import find_matching_customer, normalize_email, normalize_phone
from app.services.integration_connection_service import IntegrationConnectionService

_PROVIDER = "xero"
_refresh_locks: dict[uuid.UUID, asyncio.Lock] = {}  # per tenant, per process: two refreshes with one single-use refresh token would burn it


class XeroNotConnectedError(Exception):
    pass


@dataclass
class XeroImportResult:
    contacts_seen: int = 0
    created: int = 0
    matched: int = 0
    already_linked: int = 0
    skipped: int = 0


class XeroImportService:
    def __init__(self, session_factory: async_sessionmaker, connection_service: IntegrationConnectionService) -> None:
        self._session_factory = session_factory
        self._connection_service = connection_service

    async def _load_credential(self, tenant_id: uuid.UUID) -> dict:
        connection = await self._connection_service.get_connection(tenant_id, _PROVIDER)
        if connection is None or connection.status != ConnectionStatus.CONNECTED or not connection.encrypted_credential:
            raise XeroNotConnectedError("Xero is not connected for this tenant. Connect it in Integrations first.")
        credential = decrypt_credential(connection.encrypted_credential)
        if not credential.get("xero_tenant_id") or not credential.get("refresh_token"):
            raise XeroNotConnectedError("The stored Xero connection is incomplete. Reconnect Xero.")
        return credential

    async def _fresh_credential(self, tenant_id: uuid.UUID, client: XeroClient, *, force: bool = False) -> dict:
        """Loads the credential and refreshes if needed, persisting rotated tokens under a per-tenant lock (re-reading inside it, so a concurrent refresh is reused)."""
        lock = _refresh_locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            credential = await self._load_credential(tenant_id)
            try:
                refreshed = await xc.ensure_fresh_credential(client, credential, force=force)
            except XeroAPIError as exc:
                if exc.error_type == XeroErrorType.REFRESH_REVOKED:
                    raise XeroNotConnectedError("The Xero login expired or was revoked. Reconnect Xero.") from exc
                raise
            if refreshed:
                await self._connection_service.update_credential(tenant_id, _PROVIDER, credential)
            return credential

    async def import_contacts(self, tenant_id: uuid.UUID, *, max_records: int = 300) -> XeroImportResult:
        client = XeroClient()
        credential = await self._fresh_credential(tenant_id, client)
        try:
            contacts = await client.list_contacts(access_token=credential["access_token"], xero_tenant_id=credential["xero_tenant_id"], max_records=max_records)
        except XeroAPIError as exc:
            if exc.error_type != XeroErrorType.AUTHENTICATION or exc.status_code == 403:
                raise
            credential = await self._fresh_credential(tenant_id, client, force=True)
            contacts = await client.list_contacts(access_token=credential["access_token"], xero_tenant_id=credential["xero_tenant_id"], max_records=max_records)

        result = XeroImportResult(contacts_seen=len(contacts))
        for contact in contacts:
            outcome = await self._import_one(tenant_id, contact)
            setattr(result, outcome, getattr(result, outcome) + 1)
        return result

    async def _import_one(self, tenant_id: uuid.UUID, contact: xc.XeroContact) -> str:
        if not contact.name or (contact.is_supplier and not contact.is_customer) or contact.status.upper() == "ARCHIVED":
            return "skipped"
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            linked = (
                await session.execute(
                    select(Customer).where(Customer.tenant_id == tenant_id, Customer.external_provider == _PROVIDER, Customer.external_id == contact.contact_id)
                )
            ).scalar_one_or_none()
            if linked is not None:
                return "already_linked"
            match = await find_matching_customer(session, tenant_id=tenant_id, email=contact.email, phone=contact.phone)
            if match is not None:
                if match.external_provider is None:  # never overwrite a link to another system
                    match.external_provider = _PROVIDER
                    match.external_id = contact.contact_id
                    await session.commit()
                return "matched"

            from app.services.consent_gate import ensure_not_gated

            await ensure_not_gated(self._session_factory, tenant_id, "customer_import_disabled_for_consent_gated_tenant")
            session.add(Customer(
                tenant_id=tenant_id, name=contact.name, email=normalize_email(contact.email), phone=contact.phone,
                phone_normalized=normalize_phone(contact.phone), external_provider=_PROVIDER, external_id=contact.contact_id,
            ))
            await session.commit()
            return "created"
