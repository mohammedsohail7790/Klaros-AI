"""Phase 4: authenticated Voice Receptionist settings + call history API."""

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import CurrentUser, require_permission
from app.api.tool_deps import get_voice_call_service
from app.models.rbac import Permission
from app.services.speech_provider import get_streaming_stt_provider, get_streaming_tts_provider
from app.services.voice_call_service import CallSessionNotFoundError, VoiceCallService

router = APIRouter(prefix="/voice", tags=["voice"])


def _settings_to_dict(s) -> dict[str, Any]:
    stt = get_streaming_stt_provider()
    tts = get_streaming_tts_provider()
    return {
        "enabled": s.enabled,
        "greeting": s.greeting,
        "business_hours_note": s.business_hours_note,
        "voice_name": s.voice_name,
        "updated_at": s.updated_at.isoformat(),
        # Phase 6: honest, request-time provider status — never a
        # hardcoded/cached "connected" claim. NOT_CONFIGURED is the truth
        # in this sandbox for both; a real deployment with real keys set
        # would see "deepgram"/"elevenlabs" here instead.
        "stt_provider": stt.name if stt.is_connected else "NOT_CONFIGURED",
        "tts_provider": tts.name if tts.is_connected else "NOT_CONFIGURED",
    }


def _call_to_dict(c) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "provider": c.provider,
        "external_call_id": c.external_call_id,
        "direction": c.direction,
        "caller_number": c.caller_number,
        "status": c.status,
        "outcome": c.outcome,
        "started_at": c.started_at.isoformat(),
        "ended_at": c.ended_at.isoformat() if c.ended_at else None,
        "customer_id": str(c.customer_id) if c.customer_id else None,
        "lead_id": str(c.lead_id) if c.lead_id else None,
        "appointment_id": str(c.appointment_id) if c.appointment_id else None,
        "handoff_requested": c.handoff_requested,
        "handoff_reason": c.handoff_reason,
        "failure_reason": c.failure_reason,
        "transcript": c.transcript,
        # Phase 5: the booking sub-state-machine, if this call ever entered
        # it — never raw internal fields beyond what's useful for the owner
        # to see what happened (state, service, selected time, any
        # appointment id already covered by the top-level field above).
        "booking": (
            {
                "state": (c.engine_state.get("booking") or {}).get("state"),
                "service_type": (c.engine_state.get("booking") or {}).get("service_type"),
                "service_summary": (c.engine_state.get("booking") or {}).get("service_summary"),
                "selected_slot": (c.engine_state.get("booking") or {}).get("selected_slot"),
            }
            if c.engine_state.get("booking")
            else None
        ),
        # Phase 6: real per-turn latency, recorded by
        # app/services/voice_realtime_service.py — empty until a real-time
        # turn has actually run; never a fabricated/estimated value.
        "latency_ms": c.engine_state.get("latency_ms") or [],
    }


@router.get("/settings")
async def get_settings_endpoint(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_VOICE_CALLS)),
    service: VoiceCallService = Depends(get_voice_call_service),
) -> dict[str, Any]:
    settings = await service.get_settings(current_user.tenant_id)
    return _settings_to_dict(settings)


class UpdateVoiceSettingsRequest(BaseModel):
    enabled: bool | None = None
    greeting: str | None = None
    business_hours_note: str | None = None
    voice_name: str | None = None


@router.put("/settings")
async def update_settings_endpoint(
    body: UpdateVoiceSettingsRequest,
    current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_VOICE_SETTINGS)),
    service: VoiceCallService = Depends(get_voice_call_service),
) -> dict[str, Any]:
    settings = await service.update_settings(
        current_user.tenant_id, enabled=body.enabled, greeting=body.greeting,
        business_hours_note=body.business_hours_note, voice_name=body.voice_name, actor_id=current_user.id,
    )
    return _settings_to_dict(settings)


@router.get("/calls")
async def list_calls_endpoint(
    current_user: CurrentUser = Depends(require_permission(Permission.READ_VOICE_CALLS)),
    service: VoiceCallService = Depends(get_voice_call_service),
) -> dict[str, Any]:
    calls = await service.list_calls(current_user.tenant_id)
    return {"calls": [_call_to_dict(c) for c in calls]}


@router.get("/calls/{call_id}")
async def get_call_endpoint(
    call_id: str,
    current_user: CurrentUser = Depends(require_permission(Permission.READ_VOICE_CALLS)),
    service: VoiceCallService = Depends(get_voice_call_service),
) -> dict[str, Any]:
    import uuid as _uuid
    from fastapi import HTTPException, status

    try:
        call = await service.get_call(current_user.tenant_id, _uuid.UUID(call_id))
    except (CallSessionNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _call_to_dict(call)
