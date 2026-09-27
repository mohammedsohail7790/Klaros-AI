from enum import StrEnum


class ActorType(StrEnum):
    """Who performed an audited action (section 9 / spec section 23)."""

    USER = "USER"
    AI = "AI"
    SYSTEM = "SYSTEM"
    WORKFLOW = "WORKFLOW"
    # Phase 4 (KLAROS_FINAL_AGENT_MODEL.md "What must change, precisely",
    # item 1): additive enum value only — this is a code-level StrEnum, not
    # a Postgres native enum type, so no migration is needed to add it (the
    # column storing it is a plain String, verified against
    # app/models/audit_log.py / app/models/approval.py). Distinct from AI:
    # AGENT identifies a governed `Agent`/`AgentVersion` run (which itself
    # may make AI calls as one of its capabilities), never a raw AI
    # provider call made directly by existing deterministic service logic.
    AGENT = "AGENT"
    # Phase 9 (MCP server exposure, KLAROS_FINAL_AGENT_MODEL.md's MCP-server
    # carve-out — narrower than the rejected "Klaros as MCP client"
    # direction, ADR-002): additive enum value only, same rationale as
    # AGENT above — a plain String column, no migration required. Identifies
    # a call that arrived through the Klaros MCP server on behalf of an
    # authenticated, tenant-scoped `McpClientCredential` (app/models/
    # mcp_server.py) — i.e. an external MCP client, never Klaros itself
    # acting as an MCP client (that direction remains unbuilt). Distinct
    # from AGENT: an MCP-client call carries a Role (like USER) and flows
    # through ToolRegistry.execute()'s existing RBAC/tenant/policy checks
    # exactly as a USER call would — it does NOT go through the Agent-
    # specific tool-permission/autonomy pre-checks, because an MCP client is
    # not a governed `Agent`.
    MCP_CLIENT = "MCP_CLIENT"
