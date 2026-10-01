"""Phase 10 (PHASE_10_MEDICAL_TOURISM_DOMAIN_DESIGN.md): service layer for
the Medical Tourism vertical domain. Domain logic (tenant ownership,
uniqueness, valid relationships, active/inactive behavior) lives here,
never directly in route handlers (app/api/v1/medical_tourism.py) or in
ToolRegistry tools (app/tools/builtin/medical_tourism_tools.py) — both of
those call into this one service, mirroring every other domain in this
codebase (LeadService, ReferralService, ...).

Concurrency: `create_provider` / `create_procedure` (idempotency-key
dedup) and `create_provider_procedure` (the provider+procedure uniqueness
constraint) all use the same try/insert/except-IntegrityError/rollback/
re-fetch compare-and-swap pattern LeadService.create_lead documents and
Phase 29/30's audit fixed everywhere else in this codebase — a real
concurrent duplicate submission is caught by the DB-level unique
constraint, not just a check-then-insert race.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.crm import Lead
from app.models.medical_tourism import (
    Consultation,
    ConsultationStatus,
    CredentialStatus,
    PatientLead,
    Procedure,
    ProcedureStatus,
    Provider,
    ProviderCredential,
    ProviderProcedure,
    ProviderProcedureStatus,
    ProviderStatus,
    ReferralCommission,
    ReferralCommissionBasis,
    ReferralCommissionStatus,
)
from app.models.retention import Referral


class DuplicateProviderError(Exception):
    def __init__(self, existing: Provider) -> None:
        super().__init__("Provider with this idempotency key already exists")
        self.existing = existing


class DuplicateProcedureError(Exception):
    def __init__(self, existing: Procedure) -> None:
        super().__init__("Procedure with this idempotency key already exists")
        self.existing = existing


class DuplicateOfferingError(Exception):
    def __init__(self, existing: ProviderProcedure) -> None:
        super().__init__("This provider already offers this procedure")
        self.existing = existing


class NotFoundError(Exception):
    pass


class InvalidRelationshipError(Exception):
    pass


@dataclass
class CreateProviderInput:
    name: str
    country: str
    practitioner_name: str | None = None
    city: str | None = None
    address: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    description: str | None = None
    idempotency_key: str | None = None


@dataclass
class CreateProcedureInput:
    name: str
    category: str | None = None
    description: str | None = None
    typical_destination_countries: str | None = None
    idempotency_key: str | None = None


@dataclass
class CreateOfferingInput:
    provider_id: uuid.UUID
    procedure_id: uuid.UUID
    estimated_price: Decimal | None = None
    currency: str | None = None


class MedicalTourismService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # --- Providers ----------------------------------------------------

    async def create_provider(self, tenant_id: uuid.UUID, data: CreateProviderInput) -> tuple[Provider, bool]:
        """Returns (provider, was_deduplicated)."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if data.idempotency_key:
                existing = (
                    await session.execute(
                        select(Provider).where(
                            Provider.tenant_id == tenant_id,
                            Provider.idempotency_key == data.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True

            provider = Provider(
                tenant_id=tenant_id,
                name=data.name,
                practitioner_name=data.practitioner_name,
                country=data.country.upper(),
                city=data.city,
                address=data.address,
                contact_email=data.contact_email,
                contact_phone=data.contact_phone,
                description=data.description,
                status=ProviderStatus.ACTIVE,
                idempotency_key=data.idempotency_key,
            )
            session.add(provider)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not data.idempotency_key:
                    raise
                existing = (
                    await session.execute(
                        select(Provider).where(
                            Provider.tenant_id == tenant_id,
                            Provider.idempotency_key == data.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True
                raise
            await session.refresh(provider)
            return provider, False

    async def get_provider(self, tenant_id: uuid.UUID, provider_id: uuid.UUID) -> Provider:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            provider = (
                await session.execute(
                    select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if provider is None:
                raise NotFoundError(f"Provider {provider_id} not found")
            return provider

    async def list_providers(
        self,
        tenant_id: uuid.UUID,
        *,
        country: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Provider], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(Provider).where(Provider.tenant_id == tenant_id)
            count_query = select(func.count()).select_from(Provider).where(Provider.tenant_id == tenant_id)
            if country:
                query = query.where(Provider.country == country.upper())
                count_query = count_query.where(Provider.country == country.upper())
            if status:
                query = query.where(Provider.status == status)
                count_query = count_query.where(Provider.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (await session.execute(query.order_by(Provider.created_at.desc()).limit(limit).offset(offset)))
                .scalars()
                .all()
            )
            return list(rows), total

    async def update_provider(
        self, tenant_id: uuid.UUID, provider_id: uuid.UUID, updates: dict
    ) -> Provider:
        allowed = {
            "name",
            "practitioner_name",
            "country",
            "city",
            "address",
            "contact_email",
            "contact_phone",
            "description",
            "status",
        }
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            provider = (
                await session.execute(
                    select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if provider is None:
                raise NotFoundError(f"Provider {provider_id} not found")
            for key, value in updates.items():
                if key in allowed and value is not None:
                    setattr(provider, key if key != "country" else "country", value.upper() if key == "country" else value)
            await session.commit()
            await session.refresh(provider)
            return provider

    # --- Provider credentials ------------------------------------------

    async def add_provider_credential(
        self,
        tenant_id: uuid.UUID,
        provider_id: uuid.UUID,
        credential_type: str,
        issuing_authority: str | None = None,
        credential_number: str | None = None,
        issued_date: date | None = None,
        expiry_date: date | None = None,
    ) -> ProviderCredential:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            provider = (
                await session.execute(
                    select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if provider is None:
                raise NotFoundError(f"Provider {provider_id} not found")
            credential = ProviderCredential(
                tenant_id=tenant_id,
                provider_id=provider_id,
                credential_type=credential_type,
                issuing_authority=issuing_authority,
                credential_number=credential_number,
                issued_date=issued_date,
                expiry_date=expiry_date,
                status=CredentialStatus.PENDING,
            )
            session.add(credential)
            await session.commit()
            await session.refresh(credential)
            return credential

    async def verify_provider_credential(
        self, tenant_id: uuid.UUID, credential_id: uuid.UUID, verified_by: uuid.UUID
    ) -> ProviderCredential:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            credential = (
                await session.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.id == credential_id, ProviderCredential.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if credential is None:
                raise NotFoundError(f"ProviderCredential {credential_id} not found")
            credential.status = CredentialStatus.VERIFIED
            credential.verified_by = verified_by
            credential.verified_at = datetime.now(timezone.utc)
            await session.commit()
            await session.refresh(credential)
            return credential

    async def list_provider_credentials(
        self, tenant_id: uuid.UUID, provider_id: uuid.UUID
    ) -> list[ProviderCredential]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(ProviderCredential).where(
                        ProviderCredential.tenant_id == tenant_id, ProviderCredential.provider_id == provider_id
                    )
                )
            ).scalars().all()
            return list(rows)

    # --- Procedures ------------------------------------------------------

    async def create_procedure(
        self, tenant_id: uuid.UUID, data: CreateProcedureInput
    ) -> tuple[Procedure, bool]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            if data.idempotency_key:
                existing = (
                    await session.execute(
                        select(Procedure).where(
                            Procedure.tenant_id == tenant_id,
                            Procedure.idempotency_key == data.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True

            procedure = Procedure(
                tenant_id=tenant_id,
                name=data.name,
                category=data.category,
                description=data.description,
                typical_destination_countries=data.typical_destination_countries,
                status=ProcedureStatus.ACTIVE,
                idempotency_key=data.idempotency_key,
            )
            session.add(procedure)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                if not data.idempotency_key:
                    raise
                existing = (
                    await session.execute(
                        select(Procedure).where(
                            Procedure.tenant_id == tenant_id,
                            Procedure.idempotency_key == data.idempotency_key,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True
                raise
            await session.refresh(procedure)
            return procedure, False

    async def get_procedure(self, tenant_id: uuid.UUID, procedure_id: uuid.UUID) -> Procedure:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            procedure = (
                await session.execute(
                    select(Procedure).where(Procedure.id == procedure_id, Procedure.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if procedure is None:
                raise NotFoundError(f"Procedure {procedure_id} not found")
            return procedure

    async def list_procedures(
        self,
        tenant_id: uuid.UUID,
        *,
        category: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Procedure], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(Procedure).where(Procedure.tenant_id == tenant_id)
            count_query = select(func.count()).select_from(Procedure).where(Procedure.tenant_id == tenant_id)
            if category:
                query = query.where(Procedure.category == category)
                count_query = count_query.where(Procedure.category == category)
            if status:
                query = query.where(Procedure.status == status)
                count_query = count_query.where(Procedure.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (await session.execute(query.order_by(Procedure.created_at.desc()).limit(limit).offset(offset)))
                .scalars()
                .all()
            )
            return list(rows), total

    # --- Provider Procedure offerings ------------------------------------

    async def create_provider_procedure(
        self, tenant_id: uuid.UUID, data: CreateOfferingInput
    ) -> tuple[ProviderProcedure, bool]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            provider = (
                await session.execute(
                    select(Provider).where(Provider.id == data.provider_id, Provider.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if provider is None:
                raise NotFoundError(f"Provider {data.provider_id} not found")
            procedure = (
                await session.execute(
                    select(Procedure).where(
                        Procedure.id == data.procedure_id, Procedure.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if procedure is None:
                raise NotFoundError(f"Procedure {data.procedure_id} not found")

            existing = (
                await session.execute(
                    select(ProviderProcedure).where(
                        ProviderProcedure.tenant_id == tenant_id,
                        ProviderProcedure.provider_id == data.provider_id,
                        ProviderProcedure.procedure_id == data.procedure_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, True

            offering = ProviderProcedure(
                tenant_id=tenant_id,
                provider_id=data.provider_id,
                procedure_id=data.procedure_id,
                estimated_price=data.estimated_price,
                currency=data.currency,
                status=ProviderProcedureStatus.ACTIVE,
            )
            session.add(offering)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(ProviderProcedure).where(
                            ProviderProcedure.tenant_id == tenant_id,
                            ProviderProcedure.provider_id == data.provider_id,
                            ProviderProcedure.procedure_id == data.procedure_id,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return existing, True
                raise
            await session.refresh(offering)
            return offering, False

    async def list_offerings(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_id: uuid.UUID | None = None,
        procedure_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ProviderProcedure], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(ProviderProcedure).where(ProviderProcedure.tenant_id == tenant_id)
            count_query = (
                select(func.count()).select_from(ProviderProcedure).where(ProviderProcedure.tenant_id == tenant_id)
            )
            if provider_id:
                query = query.where(ProviderProcedure.provider_id == provider_id)
                count_query = count_query.where(ProviderProcedure.provider_id == provider_id)
            if procedure_id:
                query = query.where(ProviderProcedure.procedure_id == procedure_id)
                count_query = count_query.where(ProviderProcedure.procedure_id == procedure_id)
            if status:
                query = query.where(ProviderProcedure.status == status)
                count_query = count_query.where(ProviderProcedure.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (
                    await session.execute(
                        query.order_by(ProviderProcedure.created_at.desc()).limit(limit).offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows), total

    # --- Patient leads (extends Lead) -------------------------------------

    async def create_patient_lead(
        self,
        tenant_id: uuid.UUID,
        lead_id: uuid.UUID,
        procedure_id: uuid.UUID | None = None,
        preferred_destination_country: str | None = None,
        medical_history_summary: str | None = None,
        travel_start_date: date | None = None,
        travel_end_date: date | None = None,
        has_insurance: bool | None = None,
        insurance_notes: str | None = None,
    ) -> PatientLead:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            lead = (
                await session.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
            ).scalar_one_or_none()
            if lead is None:
                raise NotFoundError(f"Lead {lead_id} not found")
            if procedure_id is not None:
                procedure = (
                    await session.execute(
                        select(Procedure).where(Procedure.id == procedure_id, Procedure.tenant_id == tenant_id)
                    )
                ).scalar_one_or_none()
                if procedure is None:
                    raise NotFoundError(f"Procedure {procedure_id} not found")

            existing = (
                await session.execute(
                    select(PatientLead).where(PatientLead.tenant_id == tenant_id, PatientLead.lead_id == lead_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise InvalidRelationshipError(f"Lead {lead_id} already has a PatientLead extension")

            patient_lead = PatientLead(
                tenant_id=tenant_id,
                lead_id=lead_id,
                procedure_id=procedure_id,
                preferred_destination_country=(
                    preferred_destination_country.upper() if preferred_destination_country else None
                ),
                medical_history_summary=medical_history_summary,
                travel_start_date=travel_start_date,
                travel_end_date=travel_end_date,
                has_insurance=has_insurance,
                insurance_notes=insurance_notes,
            )
            session.add(patient_lead)
            await session.commit()
            await session.refresh(patient_lead)
            return patient_lead

    async def get_patient_lead(self, tenant_id: uuid.UUID, patient_lead_id: uuid.UUID) -> PatientLead:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            patient_lead = (
                await session.execute(
                    select(PatientLead).where(
                        PatientLead.id == patient_lead_id, PatientLead.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if patient_lead is None:
                raise NotFoundError(f"PatientLead {patient_lead_id} not found")
            return patient_lead

    async def get_patient_lead_by_lead_id(self, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> PatientLead:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            patient_lead = (
                await session.execute(
                    select(PatientLead).where(PatientLead.tenant_id == tenant_id, PatientLead.lead_id == lead_id)
                )
            ).scalar_one_or_none()
            if patient_lead is None:
                raise NotFoundError(f"PatientLead for Lead {lead_id} not found")
            return patient_lead

    async def list_patient_leads(
        self,
        tenant_id: uuid.UUID,
        *,
        procedure_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PatientLead], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(PatientLead).where(PatientLead.tenant_id == tenant_id)
            count_query = select(func.count()).select_from(PatientLead).where(PatientLead.tenant_id == tenant_id)
            if procedure_id:
                query = query.where(PatientLead.procedure_id == procedure_id)
                count_query = count_query.where(PatientLead.procedure_id == procedure_id)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (await session.execute(query.order_by(PatientLead.created_at.desc()).limit(limit).offset(offset)))
                .scalars()
                .all()
            )
            return list(rows), total

    # --- Consultations (extends Appointment) ------------------------------

    async def create_consultation(
        self,
        tenant_id: uuid.UUID,
        appointment_id: uuid.UUID,
        provider_id: uuid.UUID,
        procedure_id: uuid.UUID | None = None,
        notes: str | None = None,
    ) -> Consultation:
        from app.models.crm import Appointment

        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            appointment = (
                await session.execute(
                    select(Appointment).where(
                        Appointment.id == appointment_id, Appointment.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if appointment is None:
                raise NotFoundError(f"Appointment {appointment_id} not found")
            provider = (
                await session.execute(
                    select(Provider).where(Provider.id == provider_id, Provider.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if provider is None:
                raise NotFoundError(f"Provider {provider_id} not found")

            existing = (
                await session.execute(
                    select(Consultation).where(
                        Consultation.tenant_id == tenant_id, Consultation.appointment_id == appointment_id
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise InvalidRelationshipError(
                    f"Appointment {appointment_id} already has a Consultation extension"
                )

            consultation = Consultation(
                tenant_id=tenant_id,
                appointment_id=appointment_id,
                provider_id=provider_id,
                procedure_id=procedure_id,
                status=ConsultationStatus.SCHEDULED,
                notes=notes,
            )
            session.add(consultation)
            await session.commit()
            await session.refresh(consultation)
            return consultation

    async def get_consultation(self, tenant_id: uuid.UUID, consultation_id: uuid.UUID) -> Consultation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            consultation = (
                await session.execute(
                    select(Consultation).where(
                        Consultation.id == consultation_id, Consultation.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if consultation is None:
                raise NotFoundError(f"Consultation {consultation_id} not found")
            return consultation

    async def list_consultations(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Consultation], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(Consultation).where(Consultation.tenant_id == tenant_id)
            count_query = select(func.count()).select_from(Consultation).where(Consultation.tenant_id == tenant_id)
            if provider_id:
                query = query.where(Consultation.provider_id == provider_id)
                count_query = count_query.where(Consultation.provider_id == provider_id)
            if status:
                query = query.where(Consultation.status == status)
                count_query = count_query.where(Consultation.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (
                    await session.execute(
                        query.order_by(Consultation.created_at.desc()).limit(limit).offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows), total

    async def update_consultation(
        self,
        tenant_id: uuid.UUID,
        consultation_id: uuid.UUID,
        *,
        status: str | None = None,
        notes: str | None = None,
    ) -> Consultation:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            consultation = (
                await session.execute(
                    select(Consultation).where(
                        Consultation.id == consultation_id, Consultation.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if consultation is None:
                raise NotFoundError(f"Consultation {consultation_id} not found")
            if status is not None:
                consultation.status = status
            if notes is not None:
                consultation.notes = notes
            await session.commit()
            await session.refresh(consultation)
            return consultation

    # --- Referral commissions (extends Referral) --------------------------

    async def create_referral_commission(
        self,
        tenant_id: uuid.UUID,
        referral_id: uuid.UUID,
        currency: str,
        provider_id: uuid.UUID | None = None,
        basis: str = ReferralCommissionBasis.PERCENTAGE,
        commission_percentage: Decimal | None = None,
        flat_amount: Decimal | None = None,
    ) -> ReferralCommission:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            referral = (
                await session.execute(
                    select(Referral).where(Referral.id == referral_id, Referral.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if referral is None:
                raise NotFoundError(f"Referral {referral_id} not found")

            existing = (
                await session.execute(
                    select(ReferralCommission).where(
                        ReferralCommission.tenant_id == tenant_id,
                        ReferralCommission.referral_id == referral_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise InvalidRelationshipError(
                    f"Referral {referral_id} already has a ReferralCommission extension"
                )

            computed_amount = None
            if basis == ReferralCommissionBasis.PERCENTAGE and commission_percentage is not None:
                revenue = referral.revenue_amount or Decimal("0")
                computed_amount = (revenue * commission_percentage / Decimal("100")).quantize(Decimal("0.01"))
            elif basis == ReferralCommissionBasis.FLAT and flat_amount is not None:
                computed_amount = flat_amount

            commission = ReferralCommission(
                tenant_id=tenant_id,
                referral_id=referral_id,
                provider_id=provider_id,
                basis=basis,
                commission_percentage=commission_percentage,
                flat_amount=flat_amount,
                computed_amount=computed_amount,
                currency=currency.upper(),
                status=ReferralCommissionStatus.PENDING,
            )
            session.add(commission)
            await session.commit()
            await session.refresh(commission)
            return commission

    async def get_referral_commission(
        self, tenant_id: uuid.UUID, commission_id: uuid.UUID
    ) -> ReferralCommission:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            commission = (
                await session.execute(
                    select(ReferralCommission).where(
                        ReferralCommission.id == commission_id, ReferralCommission.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if commission is None:
                raise NotFoundError(f"ReferralCommission {commission_id} not found")
            return commission

    async def list_referral_commissions(
        self,
        tenant_id: uuid.UUID,
        *,
        provider_id: uuid.UUID | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ReferralCommission], int]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            from sqlalchemy import func

            query = select(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id)
            count_query = (
                select(func.count()).select_from(ReferralCommission).where(ReferralCommission.tenant_id == tenant_id)
            )
            if provider_id:
                query = query.where(ReferralCommission.provider_id == provider_id)
                count_query = count_query.where(ReferralCommission.provider_id == provider_id)
            if status:
                query = query.where(ReferralCommission.status == status)
                count_query = count_query.where(ReferralCommission.status == status)
            total = (await session.execute(count_query)).scalar_one()
            rows = (
                (
                    await session.execute(
                        query.order_by(ReferralCommission.created_at.desc()).limit(limit).offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows), total

    async def update_referral_commission_status(
        self, tenant_id: uuid.UUID, commission_id: uuid.UUID, status: str
    ) -> ReferralCommission:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            commission = (
                await session.execute(
                    select(ReferralCommission).where(
                        ReferralCommission.id == commission_id, ReferralCommission.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if commission is None:
                raise NotFoundError(f"ReferralCommission {commission_id} not found")
            commission.status = status
            await session.commit()
            await session.refresh(commission)
            return commission


# ---------------------------------------------------------------------------
# Phase 11 (Website Builder, PHASE_11_WEBSITE_BUILDER_DESIGN.md §11/§12):
# this vertical's own registration of a generic Website Builder data
# provider — the ONLY place Medical Tourism content touches the Website
# Builder. Neither app/services/website_renderer.py nor
# app/services/website_generation_service.py imports this module or any
# Medical-Tourism-specific name; they only ever look up a `provider_key`
# string in app/services/website_data_providers.py's registry. This keeps
# the generic renderer/generation code free of any vertical-name branch
# (see tests/test_website_no_vertical_hardcoding.py).
# ---------------------------------------------------------------------------

from app.db.session import async_session_maker as _async_session_maker  # noqa: E402
from app.services.website_data_providers import register_website_data_provider  # noqa: E402


async def _provide_website_provider_directory(tenant_id: uuid.UUID, params: dict) -> dict:
    """Returns a generic {"items": [...]} shape — plain strings/numbers
    only, no HTML — for the PROVIDER_DIRECTORY website component. Each
    item is deliberately reshaped into vertical-agnostic keys (name/
    location/description/offerings) rather than exposing this module's own
    internal column names, so the renderer never needs to know anything
    Medical-Tourism-specific."""
    service = MedicalTourismService(_async_session_maker)
    limit = int(params.get("limit", 12)) if isinstance(params.get("limit", 12), (int, float, str)) else 12
    limit = max(1, min(limit, 50))
    providers, _total = await service.list_providers(
        tenant_id, status=ProviderStatus.ACTIVE, limit=limit, offset=0
    )
    items = []
    for p in providers:
        offerings, _ = await service.list_offerings(tenant_id, provider_id=p.id, status="ACTIVE", limit=10)
        procedure_ids = [o.procedure_id for o in offerings]
        procedure_names: dict[uuid.UUID, str] = {}
        if procedure_ids:
            async with _async_session_maker() as session:
                await set_tenant_context(session, tenant_id)
                rows = (
                    await session.execute(
                        select(Procedure).where(
                            Procedure.tenant_id == tenant_id, Procedure.id.in_(procedure_ids)
                        )
                    )
                ).scalars().all()
                procedure_names = {r.id: r.name for r in rows}
        items.append(
            {
                "name": p.name,
                "location": ", ".join(x for x in [p.city, p.country] if x),
                "description": p.description or "",
                "offerings": [
                    {
                        "name": procedure_names.get(o.procedure_id, "Procedure"),
                        "price": str(o.estimated_price) if o.estimated_price is not None else None,
                        "currency": o.currency,
                    }
                    for o in offerings
                ],
            }
        )
    return {"items": items}


async def _provide_website_procedure_list(tenant_id: uuid.UUID, params: dict) -> dict:
    service = MedicalTourismService(_async_session_maker)
    limit = int(params.get("limit", 12)) if isinstance(params.get("limit", 12), (int, float, str)) else 12
    limit = max(1, min(limit, 50))
    procedures, _total = await service.list_procedures(tenant_id, status=ProcedureStatus.ACTIVE, limit=limit)
    return {
        "items": [
            {"name": proc.name, "category": proc.category or "", "description": proc.description or ""}
            for proc in procedures
        ]
    }


register_website_data_provider("medical_tourism.provider_directory", _provide_website_provider_directory)
register_website_data_provider("medical_tourism.procedure_catalog", _provide_website_procedure_list)
