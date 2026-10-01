"""Phase 4: CallSession / VoiceReceptionistSettings persistence — the ONE
place anything reads/writes this state (mirrors every other service in
this codebase's "one service per model" convention)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.voice import CallSession, CallStatus, VoiceReceptionistSettings


class CallSessionNotFoundError(Exception):
    pass


class VoiceCallService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_settings(self, tenant_id: uuid.UUID) -> VoiceReceptionistSettings:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(VoiceReceptionistSettings).where(VoiceReceptionistSettings.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing
            # Real default row, honestly disabled until the owner opts in
            # — never enabled-by-default with unset provider credentials.
            row = VoiceReceptionistSettings(tenant_id=tenant_id, enabled=False)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update_settings(
        self, tenant_id: uuid.UUID, *, enabled: bool | None = None, greeting: str | None = None,
        business_hours_note: str | None = None, voice_name: str | None = None, actor_id: uuid.UUID | None = None,
    ) -> VoiceReceptionistSettings:
        await self.get_settings(tenant_id)  # ensures a row exists
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            row = (
                await session.execute(
                    select(VoiceReceptionistSettings).where(VoiceReceptionistSettings.tenant_id == tenant_id)
                )
            ).scalar_one()
            if enabled is not None:
                row.enabled = enabled
            if greeting is not None:
                row.greeting = greeting
            if business_hours_note is not None:
                row.business_hours_note = business_hours_note
            if voice_name is not None:
                row.voice_name = voice_name
            row.updated_by = actor_id
            await session.commit()
            await session.refresh(row)
            return row

    async def get_or_create_call(
        self, tenant_id: uuid.UUID, *, provider: str, external_call_id: str, caller_number: str | None,
    ) -> tuple[CallSession, bool]:
        """Idempotent against repeated provider webhook delivery — same
        (provider, external_call_id) never creates a second CallSession."""
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            existing = (
                await session.execute(
                    select(CallSession).where(
                        CallSession.tenant_id == tenant_id, CallSession.provider == provider,
                        CallSession.external_call_id == external_call_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False

            call = CallSession(
                tenant_id=tenant_id, provider=provider, external_call_id=external_call_id,
                caller_number=caller_number, status=CallStatus.RINGING,
                started_at=datetime.now(timezone.utc), transcript=[], engine_state={},
            )
            session.add(call)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (
                    await session.execute(
                        select(CallSession).where(
                            CallSession.tenant_id == tenant_id, CallSession.provider == provider,
                            CallSession.external_call_id == external_call_id,
                        )
                    )
                ).scalar_one()
                return existing, False
            await session.refresh(call)
            return call, True

    async def get_call(self, tenant_id: uuid.UUID, call_id: uuid.UUID) -> CallSession:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            call = (
                await session.execute(
                    select(CallSession).where(CallSession.tenant_id == tenant_id, CallSession.id == call_id)
                )
            ).scalar_one_or_none()
            if call is None:
                raise CallSessionNotFoundError(f"Call {call_id} not found")
            return call

    async def list_calls(self, tenant_id: uuid.UUID, *, limit: int = 50) -> list[CallSession]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            rows = (
                await session.execute(
                    select(CallSession)
                    .where(CallSession.tenant_id == tenant_id)
                    .order_by(CallSession.started_at.desc())
                    .limit(limit)
                )
            ).scalars().all()
            return list(rows)

    async def append_transcript_turn(self, tenant_id: uuid.UUID, call_id: uuid.UUID, *, role: str, text: str) -> CallSession:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            call = (
                await session.execute(
                    select(CallSession).where(CallSession.tenant_id == tenant_id, CallSession.id == call_id)
                )
            ).scalar_one()
            call.transcript = [*call.transcript, {"role": role, "text": text}]
            await session.commit()
            await session.refresh(call)
            return call

    async def update_call(
        self, tenant_id: uuid.UUID, call_id: uuid.UUID, **fields,
    ) -> CallSession:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            call = (
                await session.execute(
                    select(CallSession).where(CallSession.tenant_id == tenant_id, CallSession.id == call_id)
                )
            ).scalar_one()
            for key, value in fields.items():
                setattr(call, key, value)
            await session.commit()
            await session.refresh(call)
            return call

    async def end_call(
        self, tenant_id: uuid.UUID, call_id: uuid.UUID, *, outcome: str, failure_reason: str | None = None,
    ) -> CallSession:
        return await self.update_call(
            tenant_id, call_id, status=CallStatus.COMPLETED, outcome=outcome,
            ended_at=datetime.now(timezone.utc), failure_reason=failure_reason,
        )
