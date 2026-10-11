"""Typed events the AI workforce platform (Halla) sends back to Klaros.

Klaros publishes them on its own event bus, tenant-scoped, under these names. The
payloads are Klaros' own DTOs — the external wire format belongs to Halla's API
contract, which is not available inside Klaros. Nothing here talks to a network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class HallaEventType(StrEnum):
    INTERACTION_STARTED = "halla.interaction.started"
    INTERACTION_COMPLETED = "halla.interaction.completed"
    LEAD_QUALIFIED = "halla.lead.qualified"
    LEAD_ESCALATED = "halla.lead.escalated"
    # `halla.appointment.requested` is deliberately NOT part of the live vocabulary: Halla's booking flow
    # has no pending-appointment state and never emits it. It stays only as a dev-simulator event type.
    APPOINTMENT_REQUESTED = "halla.appointment.requested"
    APPOINTMENT_CONFIRMED = "halla.appointment.confirmed"
    APPOINTMENT_RESCHEDULED = "halla.appointment.rescheduled"
    APPOINTMENT_CANCELLED = "halla.appointment.cancelled"


HALLA_EVENT_TYPES: tuple[str, ...] = tuple(e.value for e in HallaEventType)

SUMMARY_MAX = 2000


class InvalidWorkforceEvent(ValueError):
    pass


@dataclass(frozen=True)
class WorkforceInboundEvent:
    """One thing the workforce reports about one lead. The tenant is NEVER part of
    this object — it comes from the authenticated context of whoever delivers it."""

    type: HallaEventType
    lead_id: UUID
    interaction_id: str
    channel: str | None = None  # "voice" | "chat" | "sms" | ... (free text from the platform)
    summary: str | None = None
    outcome: dict[str, Any] = field(default_factory=dict)
    occurred_at: datetime | None = None
    simulated: bool = False

    def validate(self) -> None:
        if not self.interaction_id or len(self.interaction_id) > 120:
            raise InvalidWorkforceEvent("interaction_id is required (max 120 characters)")
        if self.summary is not None and len(self.summary) > SUMMARY_MAX:
            raise InvalidWorkforceEvent(f"summary is limited to {SUMMARY_MAX} characters")
        if self.channel is not None and len(self.channel) > 40:
            raise InvalidWorkforceEvent("channel is limited to 40 characters")


# --- the interaction state model --------------------------------------------------

NOT_CONNECTED = "NOT_CONNECTED"
CONFIGURATION_REQUIRED = "CONFIGURATION_REQUIRED"
WAITING_FOR_HALLA = "WAITING_FOR_HALLA"
IN_PROGRESS = "IN_PROGRESS"
QUALIFICATION_PENDING = "QUALIFICATION_PENDING"
QUALIFIED = "QUALIFIED"
ESCALATED = "ESCALATED"
APPOINTMENT_REQUESTED = "APPOINTMENT_REQUESTED"
APPOINTMENT_CONFIRMED = "APPOINTMENT_CONFIRMED"
APPOINTMENT_CANCELLED = "APPOINTMENT_CANCELLED"

INTERACTION_LABELS: dict[str, str] = {
    NOT_CONNECTED: "Halla not connected",
    CONFIGURATION_REQUIRED: "Halla needs configuration",
    WAITING_FOR_HALLA: "Waiting for Halla",
    IN_PROGRESS: "Halla is talking to the customer",
    QUALIFICATION_PENDING: "Qualification pending",
    QUALIFIED: "Qualified",
    ESCALATED: "Escalated to a person",
    APPOINTMENT_REQUESTED: "Appointment requested",
    APPOINTMENT_CONFIRMED: "Appointment confirmed",
    APPOINTMENT_CANCELLED: "Appointment cancelled",
}

_STATE_AFTER: dict[str, str] = {
    HallaEventType.INTERACTION_STARTED.value: IN_PROGRESS,
    HallaEventType.INTERACTION_COMPLETED.value: QUALIFICATION_PENDING,
    HallaEventType.LEAD_QUALIFIED.value: QUALIFIED,
    HallaEventType.LEAD_ESCALATED.value: ESCALATED,
    HallaEventType.APPOINTMENT_REQUESTED.value: APPOINTMENT_REQUESTED,
    HallaEventType.APPOINTMENT_CONFIRMED.value: APPOINTMENT_CONFIRMED,
    HallaEventType.APPOINTMENT_RESCHEDULED.value: APPOINTMENT_CONFIRMED,  # still booked, at a new time
    HallaEventType.APPOINTMENT_CANCELLED.value: APPOINTMENT_CANCELLED,
}

EVENT_TEXT: dict[str, str] = {
    HallaEventType.INTERACTION_STARTED.value: "Halla started a conversation",
    HallaEventType.INTERACTION_COMPLETED.value: "Halla finished the conversation",
    HallaEventType.LEAD_QUALIFIED.value: "Halla qualified the lead",
    HallaEventType.LEAD_ESCALATED.value: "Halla escalated the lead to a person",
    HallaEventType.APPOINTMENT_REQUESTED.value: "Halla recorded an appointment request",
    HallaEventType.APPOINTMENT_CONFIRMED.value: "Halla confirmed an appointment",
    HallaEventType.APPOINTMENT_RESCHEDULED.value: "Halla rescheduled an appointment",
    HallaEventType.APPOINTMENT_CANCELLED.value: "Halla cancelled an appointment",
}


def derive_interaction_state(
    workforce_status: str, events: list[tuple[str, datetime]]
) -> str:
    """The lead's AI-interaction state. Pure: same inputs, same answer.

    Recorded events always win (they are what actually happened). With none, the state
    is the truth about the workforce itself — Klaros never implies a conversation that
    did not occur.
    """
    state: str | None = None
    for etype, _at in sorted(events, key=lambda e: e[1]):
        # A call that finishes after (or while) it was escalated is still an escalated lead: Halla reports the
        # escalation both inside `call.completed` and as its own event, in either order, so completing a call
        # must never quietly un-escalate it. A later qualification or booking does move it on.
        if etype == HallaEventType.INTERACTION_COMPLETED.value and state == ESCALATED:
            continue
        state = _STATE_AFTER.get(etype, state)
    if state is not None:
        return state
    if workforce_status == "CONNECTED":
        return WAITING_FOR_HALLA
    if workforce_status in ("CONFIGURATION_REQUIRED", "NEEDS_ATTENTION"):
        return CONFIGURATION_REQUIRED
    return NOT_CONNECTED
