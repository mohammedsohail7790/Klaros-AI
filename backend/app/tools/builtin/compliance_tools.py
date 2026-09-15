"""Business compliance tools — licenses, insurance, bonds, certifications
with real expiry dates. See app/services/license_service.py."""

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.models.compliance import License
from app.models.rbac import Permission
from app.services.license_service import LicenseNotFoundError, LicenseService
from app.tools.base import ExecutionContext, Tool


def _license_to_dict(lic: License) -> dict[str, Any]:
    return {
        "id": str(lic.id),
        "type": lic.type,
        "name": lic.name,
        "license_number": lic.license_number,
        "issuing_authority": lic.issuing_authority,
        "holder_name": lic.holder_name,
        "holder_user_id": str(lic.holder_user_id) if lic.holder_user_id else None,
        "issue_date": lic.issue_date.isoformat() if lic.issue_date else None,
        "expiry_date": lic.expiry_date.isoformat(),
        "status": lic.status,
        "document_url": lic.document_url,
        "notes": lic.notes,
    }


class CreateLicenseInput(BaseModel):
    type: str
    name: str
    license_number: str | None = None
    issuing_authority: str | None = None
    holder_name: str | None = None
    holder_user_id: uuid.UUID | None = None
    issue_date: date | None = None
    expiry_date: date
    document_url: str | None = None
    notes: str | None = None


class LicenseOutput(BaseModel):
    license: dict[str, Any]


class CreateLicense(Tool):
    name = "compliance.create_license"
    description = "Record a business license, insurance policy, bond, or certification with its expiry date."
    input_schema = CreateLicenseInput
    output_schema = LicenseOutput
    required_permission = Permission.MANAGE_COMPLIANCE

    def __init__(self, license_service: LicenseService) -> None:
        self._license_service = license_service

    async def execute(self, input: CreateLicenseInput, context: ExecutionContext) -> LicenseOutput:
        lic = await self._license_service.create_license(
            context.tenant_id,
            type=input.type, name=input.name, license_number=input.license_number,
            issuing_authority=input.issuing_authority, holder_name=input.holder_name,
            holder_user_id=input.holder_user_id, issue_date=input.issue_date, expiry_date=input.expiry_date,
            document_url=input.document_url, notes=input.notes,
        )
        return LicenseOutput(license=_license_to_dict(lic))


class ListLicensesInput(BaseModel):
    status: str | None = None
    type: str | None = None


class ListLicensesOutput(BaseModel):
    licenses: list[dict[str, Any]]


class ListLicenses(Tool):
    name = "compliance.list_licenses"
    description = "List licenses/insurance/certifications, optionally filtered by status or type."
    input_schema = ListLicensesInput
    output_schema = ListLicensesOutput
    required_permission = Permission.READ_COMPLIANCE

    def __init__(self, license_service: LicenseService) -> None:
        self._license_service = license_service

    async def execute(self, input: ListLicensesInput, context: ExecutionContext) -> ListLicensesOutput:
        rows = await self._license_service.list_licenses(context.tenant_id, status=input.status, type=input.type)
        return ListLicensesOutput(licenses=[_license_to_dict(lic) for lic in rows])


class RenewLicenseInput(BaseModel):
    license_id: uuid.UUID
    issue_date: date | None = None
    expiry_date: date
    document_url: str | None = None


class RenewLicense(Tool):
    name = "compliance.renew_license"
    description = "Renew a license/insurance/certification with a new expiry date, clearing any expiring/expired status."
    input_schema = RenewLicenseInput
    output_schema = LicenseOutput
    required_permission = Permission.MANAGE_COMPLIANCE

    def __init__(self, license_service: LicenseService) -> None:
        self._license_service = license_service

    async def execute(self, input: RenewLicenseInput, context: ExecutionContext) -> LicenseOutput:
        try:
            lic = await self._license_service.renew(
                context.tenant_id, input.license_id,
                issue_date=input.issue_date, expiry_date=input.expiry_date, document_url=input.document_url,
            )
        except LicenseNotFoundError as e:
            raise ValueError(str(e)) from e
        return LicenseOutput(license=_license_to_dict(lic))


class EmptyInput(BaseModel):
    pass


class DetectExpiringOutput(BaseModel):
    newly_expiring_soon: list[str]
    newly_expired: list[str]


class DetectExpiring(Tool):
    """Phase: deterministic license-expiry sweep — same real pattern as
    quotes.detect_expired_quotes/contracts.detect_pending. Production
    would call this from a scheduler; no such scheduler exists yet, so
    it's exposed here to run manually/on a client-side poll."""

    name = "compliance.detect_expiring"
    description = "Find licenses/insurance within 30 days of expiry or past it, and flag them."
    input_schema = EmptyInput
    output_schema = DetectExpiringOutput
    required_permission = Permission.MANAGE_COMPLIANCE

    def __init__(self, license_service: LicenseService) -> None:
        self._license_service = license_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> DetectExpiringOutput:
        result = await self._license_service.detect_expiring(context.tenant_id)
        return DetectExpiringOutput(**result)
