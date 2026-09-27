"""Phase 9: the ONE MCP protocol endpoint — `POST /api/v1/mcp`. Everything
about how a call gets governed lives in `app/mcp/protocol.py`
(`McpProtocolHandler`); this router is deliberately thin — auth
dependency in, raw body in, JSON-RPC envelope out (or a 204/200-with-no-body
for a notification, per JSON-RPC 2.0)."""

import json

from fastapi import APIRouter, Depends, Request, Response

from app.api.mcp_deps import get_mcp_protocol_handler, get_mcp_request_auth
from app.mcp.protocol import McpProtocolHandler, McpRequestAuth

router = APIRouter(prefix="/mcp", tags=["mcp"])


@router.post("")
async def mcp_rpc(
    request: Request,
    auth: McpRequestAuth = Depends(get_mcp_request_auth),
    handler: McpProtocolHandler = Depends(get_mcp_protocol_handler),
) -> Response:
    raw_body = await request.body()
    result = await handler.handle_body(raw_body, auth)
    if result is None:
        # A JSON-RPC notification (e.g. notifications/initialized) never
        # gets a response body.
        return Response(status_code=204)
    return Response(content=json.dumps(result), media_type="application/json")
