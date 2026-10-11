"""AI Workforce integration boundary (Klaros side only).

Halla AI is a SEPARATE platform that provides the AI workforce — voice,
inbound/outbound calls, qualification, booking, support. Klaros does not
contain, copy or re-implement any of it. This package only defines the
contract Klaros expects an AI-workforce adapter to satisfy, and the honest
status Klaros reports until such an adapter is actually wired in.
"""

from app.integrations.workforce.contract import (
    WORKFORCE_CAPABILITIES,
    LeadSyncResult,
    OutboundCallResult,
    WorkforceAgent,
    WorkforceAgentSpec,
    WorkforceCapability,
    WorkforceIntegration,
    WorkforceNotConnectedError,
    WorkforceStatus,
    WorkforceStatusReport,
    WorkforceUnavailableError,
)
from app.integrations.workforce.registry import dev_simulator_enabled, get_workforce_integration, halla_enabled

__all__ = [
    "LeadSyncResult",
    "OutboundCallResult",
    "WORKFORCE_CAPABILITIES",
    "WorkforceAgent",
    "WorkforceNotConnectedError",
    "WorkforceUnavailableError",
    "WorkforceAgentSpec",
    "WorkforceCapability",
    "WorkforceIntegration",
    "WorkforceStatus",
    "WorkforceStatusReport",
    "dev_simulator_enabled",
    "get_workforce_integration",
    "halla_enabled",
]
