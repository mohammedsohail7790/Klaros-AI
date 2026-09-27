"""Phase 9: admin-facing endpoints for the MCP exposure allowlist and
external-client credential lifecycle. Ordinary human auth (existing JWT
`CurrentUser`), gated on `Permission.MANAGE_MCP_SERVER` (OWNER/ADMIN only
— see app/models/rbac.py's comment on why this is not split into a
separate read permission). These endpoints never execute a tool and never
touch `ToolRegistry.execute()` — they only manage which tools an external
MCP client MAY reach and who holds a credential to try.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.mcp_deps import get_mcp_credential_service, get_mcp_exposure_service
from app.api.tool_deps import get_tool_registry
from app.models.audit_log import AuditLog
from app.models.actor import ActorType
from app.models.rbac import Permission, Role
from app.db.session import async_session_maker
from app.services.mcp_service import McpCredentialError, McpCredentialService, McpExposureService
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/mcp-admin", tags=["mcp-admin"])

_require_manage_mcp = require_permission(Permission.MANAGE_MCP_SERVER)


class ExposureResponse(BaseModel):
    id: uuid.UUID
    tool_name: str
    enabled: bool


class SetExposureRequest(BaseModel):
    tool_name: str
    enabled: bool = True


class AvailableToolResponse(BaseModel):
    name: str
    description: str
    required_permission: str | None


class IssueCredentialRequest(BaseModel):
    name: str
    role: Role


class IssueCredentialResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: Role
    token: str  # shown exactly once — never retrievable again


class CredentialResponse(BaseModel):
    id: uuid.UUID
    name: str
    role: Role
    status: str
    token_prefix: str


async def _write_admin_audit(
    current_user: CurrentUser, *, action: str, tool_name: str | None = None
) -> None:
    async with async_session_maker() as session:
        session.add(
            AuditLog(
                tenant_id=current_user.tenant_id,
                actor_type=ActorType.USER,
                actor_id=current_user.id,
                action=action,
                tool=tool_name,
                result="success",
            )
        )
        await session.commit()


@router.get("/exposures", response_model=list[ExposureResponse])
async def list_exposures(
    current_user: CurrentUser = Depends(_require_manage_mcp),
    exposure_service: McpExposureService = Depends(get_mcp_exposure_service),
) -> list[ExposureResponse]:
    rows = await exposure_service.list_exposures(current_user.tenant_id)
    return [ExposureResponse(id=r.id, tool_name=r.tool_name, enabled=r.enabled) for r in rows]


@router.get("/available-tools", response_model=list[AvailableToolResponse])
async def list_available_tools(
    current_user: CurrentUser = Depends(_require_manage_mcp),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> list[AvailableToolResponse]:
    """The full internal `ToolRegistry` catalog, for the admin to CHOOSE
    from — this endpoint itself does not expose anything; it is what the
    admin looks at before calling `PUT /mcp-admin/exposures`."""
    tools = sorted(tool_registry._tools.values(), key=lambda t: t.name)  # noqa: SLF001 — admin-only catalog read
    return [
        AvailableToolResponse(
            name=t.name,
            description=t.description,
            required_permission=t.required_permission.value if t.required_permission else None,
        )
        for t in tools
    ]


@router.put("/exposures", response_model=ExposureResponse)
async def set_exposure(
    body: SetExposureRequest,
    current_user: CurrentUser = Depends(_require_manage_mcp),
    exposure_service: McpExposureService = Depends(get_mcp_exposure_service),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
) -> ExposureResponse:
    if body.enabled:
        try:
            tool_registry.get(body.tool_name)
        except Exception as exc:  # noqa: BLE001 — ToolNotFoundError, mapped to a client-actionable 404
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown tool: {body.tool_name}"
            ) from exc
    row = await exposure_service.set_exposure(
        current_user.tenant_id, body.tool_name, body.enabled, created_by=current_user.id
    )
    await _write_admin_audit(
        current_user,
        action=f"mcp.exposure_{'enabled' if body.enabled else 'disabled'}",
        tool_name=body.tool_name,
    )
    return ExposureResponse(id=row.id, tool_name=row.tool_name, enabled=row.enabled)


@router.get("/credentials", response_model=list[CredentialResponse])
async def list_credentials(
    current_user: CurrentUser = Depends(_require_manage_mcp),
    credential_service: McpCredentialService = Depends(get_mcp_credential_service),
) -> list[CredentialResponse]:
    rows = await credential_service.list_credentials(current_user.tenant_id)
    return [
        CredentialResponse(id=r.id, name=r.name, role=Role(r.role), status=r.status, token_prefix=r.token_prefix)
        for r in rows
    ]


@router.post("/credentials", response_model=IssueCredentialResponse)
async def issue_credential(
    body: IssueCredentialRequest,
    current_user: CurrentUser = Depends(_require_manage_mcp),
    credential_service: McpCredentialService = Depends(get_mcp_credential_service),
) -> IssueCredentialResponse:
    issued = await credential_service.issue(
        current_user.tenant_id, body.name, body.role, created_by=current_user.id
    )
    await _write_admin_audit(current_user, action="mcp.credential_issued")
    return IssueCredentialResponse(id=issued.id, name=issued.name, role=issued.role, token=issued.raw_token)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_credential(
    credential_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_manage_mcp),
    credential_service: McpCredentialService = Depends(get_mcp_credential_service),
) -> None:
    try:
        await credential_service.revoke(current_user.tenant_id, credential_id)
    except McpCredentialError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _write_admin_audit(current_user, action="mcp.credential_revoked")
