"""Phase 9: dependency wiring for both MCP-server surfaces —
`app/api/v1/mcp.py` (the protocol endpoint, external-client-facing) and
`app/api/v1/mcp_admin.py` (the human admin endpoints, existing JWT auth).
Mirrors `app/api/tool_deps.py`'s lru_cache-singleton-with-override pattern
so tests can substitute an isolated ToolRegistry/session_factory exactly
like every other router already does.
"""

from functools import lru_cache

from fastapi import Depends, Header, HTTPException, status

from app.api.tool_deps import get_tool_registry
from app.db.session import async_session_maker
from app.mcp.protocol import McpProtocolHandler, McpRequestAuth
from app.services.mcp_service import McpCredentialService, McpExposureService
from app.tools.registry import ToolRegistry


@lru_cache
def get_mcp_exposure_service() -> McpExposureService:
    return McpExposureService(async_session_maker)


@lru_cache
def get_mcp_credential_service() -> McpCredentialService:
    return McpCredentialService(async_session_maker)


@lru_cache
def get_mcp_protocol_handler(
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    exposure_service: McpExposureService = Depends(get_mcp_exposure_service),
) -> McpProtocolHandler:
    return McpProtocolHandler(async_session_maker, tool_registry, exposure_service)


async def get_mcp_request_auth(
    authorization: str | None = Header(default=None),
    credential_service: McpCredentialService = Depends(get_mcp_credential_service),
) -> McpRequestAuth:
    """Authenticates an external MCP client purely from the `Authorization:
    Bearer <token>` header — never from any request body field, query
    param, or client-claimed tenant/identity. Deliberately the same
    generic 401 for "missing header", "malformed header", "unknown token",
    and "revoked token" — an external, potentially adversarial caller gets
    no oracle to distinguish these cases."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    raw_token = authorization[7:].strip()
    credential = await credential_service.authenticate(raw_token)
    if credential is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or revoked MCP credential")
    return McpRequestAuth(credential=credential)
