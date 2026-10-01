"""Phase 9: the MCP **server** protocol adapter — deliberately the smallest
slice that satisfies the spec's exposure/governance requirements.

Scope decision (per this phase's instructions — classify, don't build
everything the protocol supports):

  REQUIRED NOW      : `initialize`, `tools/list`, `tools/call` (JSON-RPC 2.0
                       over a single `POST /api/v1/mcp` endpoint — the
                       "streamable HTTP" transport shape, minus SSE
                       streaming, which nothing here needs since every tool
                       call is a single request/response, exactly like
                       every other Klaros tool invocation path).
  OPTIONAL BUT SAFE : none added — `notifications/initialized` is accepted
                       and silently ack'd (a no-op notification, per the
                       JSON-RPC spec for a request with no `id`) so
                       spec-compliant clients that send it don't get a
                       protocol error, but it is not surfaced as a feature.
  DEFERRED          : `resources/*`, `prompts/*`, subscriptions, streaming.
                       Klaros exposes governed ACTIONS (tools), not a
                       document/prompt library — no evidence in this
                       codebase's 233 tools calls for resources or prompts.
  NOT APPROPRIATE   : `sampling/*`, `elicitation/*` — both would mean the
                       MCP SERVER asking the external CLIENT to run an LLM
                       completion or collect input on the server's behalf.
                       That is backwards for this direction (Klaros is the
                       one being called, not the one delegating cognition
                       out) and has no use here.

No MCP SDK dependency was added — see PHASE_9_IMPLEMENTATION_LOG.md's
"Dependency audit" section for why a ~250-line hand-written adapter for
exactly these three methods is smaller and more auditable than a general
SDK for this minimal slice.

GOVERNING RULE (never relaxed anywhere in this file): every `tools/call`
still flows through `ToolRegistry.execute()` unchanged — this module never
calls a `Tool.execute()` directly, never constructs a second execution
path, and never widens what the authenticated credential's `Role` would
otherwise be allowed to do through any other Klaros caller.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.mcp_server import McpClientCredential
from app.models.rbac import Role
from app.services.mcp_service import McpExposureService
from app.tools.base import ExecutionContext
from app.tools.errors import (
    ToolApprovalRequiredError,
    ToolBillingLimitError,
    ToolBlockedError,
    ToolError,
    ToolKillSwitchError,
    ToolNotFoundError,
    ToolPermissionError,
    ToolValidationError,
)
from app.tools.registry import ToolRegistry

# --- Transport / resource protection (spec: "Transport / resource
# protection") --------------------------------------------------------------

# A remote MCP client is untrusted input from an external, potentially
# adversarial network peer — bounded independently of any framework-level
# body-size default, so the limit is explicit and lives with the code that
# depends on it.
MAX_REQUEST_BODY_BYTES = 256 * 1024  # 256 KiB
MAX_JSON_NESTING_DEPTH = 12
TOOL_CALL_TIMEOUT_SECONDS = 30.0

# Per-tenant concurrent tools/call cap. No existing rate/concurrency
# limiter in this codebase is per-caller-identity + per-tenant shaped (the
# only candidate, app/core/rate_limit.py, is a per-IP/per-route HTTP
# limiter for login/webhook endpoints — a different dimension entirely: it
# does not know about tenants, credentials, or tool calls). A dedicated,
# tiny in-process semaphore is the smallest correct addition; it is
# explicitly NOT a substitute for the real correctness backstops
# (ToolRegistry's own kill-switch/billing/approval checks, Phase 7/8's
# idempotency machinery) — it only bounds how many concurrent tools/call
# requests one tenant's MCP client(s) can have in flight against this
# process at once. Known limitation: process-local, not distributed across
# multiple app instances — acceptable for this minimal slice; see
# PHASE_9_IMPLEMENTATION_LOG.md §12/§19.
MAX_CONCURRENT_CALLS_PER_TENANT = 5

_tenant_semaphores: dict[uuid.UUID, asyncio.Semaphore] = {}


def _semaphore_for_tenant(tenant_id: uuid.UUID) -> asyncio.Semaphore:
    sem = _tenant_semaphores.get(tenant_id)
    if sem is None:
        sem = asyncio.Semaphore(MAX_CONCURRENT_CALLS_PER_TENANT)
        _tenant_semaphores[tenant_id] = sem
    return sem


class McpProtocolError(Exception):
    """A JSON-RPC-level error (malformed envelope, unknown method, bad
    params) — distinct from a tool-call-level error, which is always
    returned as a normal JSON-RPC *result* with `isError: true` per the MCP
    spec's convention (a failed tool call is not a transport failure)."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _max_depth(value: Any, depth: int = 0) -> int:
    if depth > MAX_JSON_NESTING_DEPTH:
        return depth
    if isinstance(value, dict):
        if not value:
            return depth
        return max(_max_depth(v, depth + 1) for v in value.values())
    if isinstance(value, list):
        if not value:
            return depth
        return max(_max_depth(v, depth + 1) for v in value)
    return depth


def _text_result(payload: dict, *, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}], "isError": is_error}


@dataclass
class McpRequestAuth:
    """The authenticated identity for one MCP request — resolved ONLY from
    the bearer token's DB lookup (`McpCredentialService.authenticate`),
    never from any client-supplied field in the JSON-RPC body itself. This
    is the one and only source of tenant_id/role for every method below."""

    credential: McpClientCredential

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.credential.tenant_id

    @property
    def role(self) -> Role:
        return Role(self.credential.role)

    def execution_context(self) -> ExecutionContext:
        return ExecutionContext(
            tenant_id=self.tenant_id,
            actor_type=ActorType.MCP_CLIENT,
            actor_id=self.credential.id,
            role=self.role,
            correlation_id=uuid.uuid4(),
        )


class McpProtocolHandler:
    def __init__(
        self, session_factory: async_sessionmaker, registry: ToolRegistry, exposure_service: McpExposureService
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._exposure_service = exposure_service

    async def handle_body(self, raw_body: bytes, auth: McpRequestAuth) -> dict | None:
        """Top-level entry point — validates transport-level bounds BEFORE
        any JSON parsing or governance logic runs, per spec ("reject
        malformed/oversized/deeply-nested payloads before they reach any
        business logic")."""
        if len(raw_body) > MAX_REQUEST_BODY_BYTES:
            return self._error_envelope(None, -32600, "Request body too large")

        try:
            envelope = json.loads(raw_body)
        except (ValueError, UnicodeDecodeError):
            return self._error_envelope(None, -32700, "Parse error")

        if not isinstance(envelope, dict):
            return self._error_envelope(None, -32600, "Invalid Request")

        if _max_depth(envelope) > MAX_JSON_NESTING_DEPTH:
            return self._error_envelope(envelope.get("id"), -32600, "Request nested too deeply")

        request_id = envelope.get("id")
        method = envelope.get("method")
        params = envelope.get("params") or {}

        if envelope.get("jsonrpc") != "2.0" or not isinstance(method, str):
            return self._error_envelope(request_id, -32600, "Invalid Request")

        # A JSON-RPC *notification* (no "id") never gets a response body —
        # `notifications/initialized` is the only one a spec-compliant MCP
        # client sends here; anything else with no id is just ack'd as a
        # no-op the same way, since there is nothing further to do.
        is_notification = "id" not in envelope

        try:
            result = await self._dispatch(method, params, auth)
        except McpProtocolError as exc:
            if is_notification:
                return None
            return self._error_envelope(request_id, exc.code, exc.message)

        if is_notification:
            return None
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _error_envelope(self, request_id: Any, code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}

    async def _dispatch(self, method: str, params: dict, auth: McpRequestAuth) -> dict:
        if method == "initialize":
            await self._audit(auth, action="mcp.client_authenticated", tool_name=None, result="success")
            return {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "klaros-mcp-server", "version": "0.1.0"},
            }
        if method == "notifications/initialized":
            return {}
        if method == "tools/list":
            return await self._tools_list(auth)
        if method == "tools/call":
            return await self._tools_call(params, auth)
        raise McpProtocolError(-32601, "Method not found")

    async def _allowed_tools(self, auth: McpRequestAuth) -> set[str]:
        return await self._exposure_service.list_enabled_tool_names(auth.tenant_id)

    async def _tools_list(self, auth: McpRequestAuth) -> dict:
        allowed = await self._allowed_tools(auth)
        tools = []
        for name in sorted(allowed):
            try:
                tool = self._registry.get(name)
            except ToolNotFoundError:
                # Exposure row exists for a tool no longer registered in
                # this process — fail closed (skip it), never invent a
                # schema for a tool that doesn't exist.
                continue
            # A caller of this credential's Role must also actually be
            # permitted the tool by ordinary RBAC — an exposure row alone
            # never grants MORE than the Role already has, so a tool this
            # role can't use is left off the list (same governance ToolRegistry
            # itself would apply at call time; listing it here would be
            # misleading, not a security hole, but honesty matters).
            if tool.required_permission is not None:
                from app.models.rbac import role_has_permission

                if not role_has_permission(auth.role, tool.required_permission):
                    continue
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description,
                    "inputSchema": tool.input_schema.model_json_schema(),
                }
            )
        return {"tools": tools}

    async def _tools_call(self, params: dict, auth: McpRequestAuth) -> dict:
        if not isinstance(params, dict):
            raise McpProtocolError(-32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not name:
            raise McpProtocolError(-32602, "Invalid params: 'name' is required")
        if not isinstance(arguments, dict):
            raise McpProtocolError(-32602, "Invalid params: 'arguments' must be an object")

        allowed = await self._allowed_tools(auth)
        if name not in allowed:
            # Deliberately the SAME message whether the tool doesn't exist,
            # isn't registered, or exists but was never exposed — no
            # information leak about the tenant's internal tool catalog to
            # an unexposed name. Audited explicitly here because this
            # denial happens BEFORE ToolRegistry.execute() ever runs, so
            # ToolRegistry's own audit trail never sees it.
            await self._audit(
                auth, action="mcp.tool_invoked", tool_name=name, result="failure", error="not_exposed"
            )
            return _text_result({"error": f"Tool '{name}' is not available via MCP for this tenant"}, is_error=True)

        try:
            tool = self._registry.get(name)
        except ToolNotFoundError:
            await self._audit(
                auth, action="mcp.tool_invoked", tool_name=name, result="failure", error="not_registered"
            )
            return _text_result({"error": f"Tool '{name}' is not available via MCP for this tenant"}, is_error=True)

        # Reuse the tool's own Pydantic input_schema (spec: "reuse existing
        # Pydantic/schema validation, don't build a parallel validator") —
        # this is a pre-check ONLY. ToolRegistry.execute() still performs
        # its own authoritative validation immediately afterward; this
        # never replaces that, it just rejects obviously-malformed input
        # before spending a governance check on it, and produces a client-
        # legible MCP tool-result error instead of an opaque 500.
        try:
            tool.input_schema.model_validate(arguments)
        except ValidationError as exc:
            return _text_result({"error": f"Invalid arguments: {exc}"}, is_error=True)

        context = auth.execution_context()
        semaphore = _semaphore_for_tenant(auth.tenant_id)
        try:
            async with semaphore:
                output = await asyncio.wait_for(
                    self._registry.execute(name, arguments, context), timeout=TOOL_CALL_TIMEOUT_SECONDS
                )
        except TimeoutError:
            await self._audit(auth, action="mcp.tool_invoked", tool_name=name, result="failure", error="timeout")
            return _text_result({"error": "Tool call timed out"}, is_error=True)
        except ToolApprovalRequiredError as exc:
            # Not a failure — the call was accepted and is now pending
            # human approval through the EXACT SAME ApprovalRequest/
            # ApprovalExecutionService flow any other caller's
            # APPROVAL_REQUIRED tool call would create. ToolRegistry.execute()
            # already wrote the audit row for this (result="pending_approval");
            # no duplicate audit write here.
            return _text_result(
                {"status": "pending_approval", "approval_request_id": str(exc.approval_request_id)},
                is_error=False,
            )
        except (
            ToolPermissionError,
            ToolBlockedError,
            ToolKillSwitchError,
            ToolValidationError,
            ToolBillingLimitError,
            ToolError,
        ) as exc:
            # ToolRegistry.execute() already audited this failure with the
            # real error detail; the message returned to the client here is
            # deliberately the same str(exc) ToolRegistry itself produces
            # for every other caller (human API, agent) — no MCP-specific
            # information disclosure, no secrets (these exceptions never
            # carry credential/token material).
            return _text_result({"error": str(exc)}, is_error=True)

        try:
            dumped = output.model_dump(mode="json")
        except Exception:  # noqa: BLE001 — defensive only; every real Tool output is a BaseModel
            dumped = {"result": str(output)}
        return _text_result(dumped, is_error=False)

    async def _audit(
        self, auth: McpRequestAuth, *, action: str, tool_name: str | None, result: str, error: str | None = None
    ) -> None:
        async with self._session_factory() as session:
            # auth.tenant_id is resolved ONLY from the authenticated
            # credential's own DB row (McpRequestAuth's docstring) — never
            # from anything in the client's JSON-RPC body — so it is safe
            # to stamp as this transaction's tenant context here.
            await set_tenant_context(session, auth.tenant_id)
            session.add(
                AuditLog(
                    tenant_id=auth.tenant_id,
                    actor_type=ActorType.MCP_CLIENT,
                    actor_id=auth.credential.id,
                    action=action,
                    tool=tool_name,
                    input_summary={"_error": error} if error else None,
                    result=result,
                )
            )
            await session.commit()
