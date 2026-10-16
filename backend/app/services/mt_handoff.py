"""Medical Tourism human handoff tasks: a person owns each case that Halla (or a safety rule) hands over, and every change is audited.

A task is created, idempotently, when a Halla event says a person is needed (`lead.escalated`, `needs_human_review`, a freshly qualified lead): one task per
(source event, kind), so a retried or redelivered event never creates a second one. It starts OPEN with no owner. A team member ACCEPTS it (becoming its owner),
then completes it; either can cancel. Only the owner (or a manager) completes someone else's accepted task. Every transition writes an audit row. Task text is
a closed code plus an optional note a PERSON types; nothing from a call or from the patient is copied into it.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.session import set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.medical_tourism_referral import HandoffTask, TaskStatus

KINDS = ("halla_handoff", "escalation", "provider_options", "referral_followup", "new_enquiry", "consultation_needed")


class TaskError(Exception):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


def _audit(session, tenant_id, actor_id, action, task: HandoffTask, extra: dict[str, Any] | None = None) -> None:
    session.add(AuditLog(tenant_id=tenant_id, actor_type=ActorType.USER if actor_id else ActorType.SYSTEM, actor_id=actor_id, action=action, entity_type="medical_tourism_handoff_task",
                         entity_id=task.id, input_summary={"kind": task.kind, "lead_id": str(task.lead_id), **(extra or {})}, result="success"))


async def ensure_task(session, tenant_id: uuid.UUID, lead_id: uuid.UUID, *, kind: str, reason_code: str | None, dedupe_key: str) -> HandoffTask:
    """Create the task for this source event and kind unless it already exists. Runs inside the caller's session/transaction."""
    existing = (await session.execute(select(HandoffTask).where(HandoffTask.tenant_id == tenant_id, HandoffTask.dedupe_key == f"{kind}:{dedupe_key}"[:160]))).scalar_one_or_none()
    if existing is not None:
        return existing
    task = HandoffTask(tenant_id=tenant_id, lead_id=lead_id, kind=kind if kind in KINDS else "halla_handoff", reason_code=(reason_code or None) and reason_code[:32],
                       status=TaskStatus.OPEN, dedupe_key=f"{kind}:{dedupe_key}"[:160])
    session.add(task)
    await session.flush()
    _audit(session, tenant_id, None, "mt.task.created", task, {"reason_code": task.reason_code})
    return task


async def _load(session, tenant_id, task_id) -> HandoffTask:
    task = (await session.execute(select(HandoffTask).where(HandoffTask.id == task_id, HandoffTask.tenant_id == tenant_id))).scalar_one_or_none()
    if task is None:
        raise TaskError("not_found", 404)
    return task


async def accept(session_factory, tenant_id: uuid.UUID, task_id: uuid.UUID, user_id: uuid.UUID) -> HandoffTask:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        task = await _load(session, tenant_id, task_id)
        if task.status != TaskStatus.OPEN:
            raise TaskError("not_open")  # already owned, done or cancelled: a second person cannot silently take it over
        task.status, task.owner_user_id, task.accepted_at = TaskStatus.ACCEPTED, user_id, datetime.now(timezone.utc)
        _audit(session, tenant_id, user_id, "mt.task.accepted", task)
        await session.commit()
        await session.refresh(task)
        return task


async def complete(session_factory, tenant_id: uuid.UUID, task_id: uuid.UUID, user_id: uuid.UUID, *, is_manager: bool = False, note: str | None = None) -> HandoffTask:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        task = await _load(session, tenant_id, task_id)
        if task.status != TaskStatus.ACCEPTED:
            raise TaskError("not_accepted")
        if task.owner_user_id != user_id and not is_manager:
            raise TaskError("not_the_owner", 403)
        task.status, task.completed_at = TaskStatus.DONE, datetime.now(timezone.utc)
        if note:
            task.note = note.strip()[:1000]
        _audit(session, tenant_id, user_id, "mt.task.completed", task)
        await session.commit()
        await session.refresh(task)
        return task


async def cancel(session_factory, tenant_id: uuid.UUID, task_id: uuid.UUID, user_id: uuid.UUID, *, is_manager: bool = False, note: str | None = None) -> HandoffTask:
    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        task = await _load(session, tenant_id, task_id)
        if task.status not in (TaskStatus.OPEN, TaskStatus.ACCEPTED):
            raise TaskError("already_closed")
        if task.status == TaskStatus.ACCEPTED and task.owner_user_id != user_id and not is_manager:
            raise TaskError("not_the_owner", 403)
        task.status, task.completed_at = TaskStatus.CANCELLED, datetime.now(timezone.utc)
        if note:
            task.note = note.strip()[:1000]
        _audit(session, tenant_id, user_id, "mt.task.cancelled", task)
        await session.commit()
        await session.refresh(task)
        return task


async def list_tasks(session_factory, tenant_id: uuid.UUID, *, status: str | None = None, lead_id: uuid.UUID | None = None, limit: int = 50, offset: int = 0) -> tuple[list[HandoffTask], int]:
    from sqlalchemy import func

    async with session_factory() as session:
        await set_tenant_context(session, tenant_id)
        q = select(HandoffTask).where(HandoffTask.tenant_id == tenant_id)
        if status:
            q = q.where(HandoffTask.status == status)
        if lead_id:
            q = q.where(HandoffTask.lead_id == lead_id)
        total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
        rows = (await session.execute(q.order_by(HandoffTask.created_at.desc()).limit(limit).offset(offset))).scalars().all()
        return list(rows), int(total)


def task_to_dict(t: HandoffTask) -> dict[str, Any]:
    return {
        "id": str(t.id), "lead_id": str(t.lead_id), "kind": t.kind, "reason_code": t.reason_code, "status": t.status,
        "owner_user_id": str(t.owner_user_id) if t.owner_user_id else None,
        "accepted_at": t.accepted_at.isoformat() if t.accepted_at else None, "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        "note": t.note, "created_at": t.created_at.isoformat() if t.created_at else None,
    }


async def close_open_case_tasks(session, tenant_id: uuid.UUID, lead_id: uuid.UUID, kinds: tuple[str, ...], reason: str, *, final_status: TaskStatus = TaskStatus.DONE) -> int:
    """The work a task asked for has happened (for example the enquiry now has a clinic referral): close the matching OPEN or ACCEPTED tasks. Runs inside the caller's session.

    `final_status` is CANCELLED when the work will never happen (the lead was marked lost or not a fit)."""
    tasks = (await session.execute(select(HandoffTask).where(HandoffTask.tenant_id == tenant_id, HandoffTask.lead_id == lead_id, HandoffTask.kind.in_(kinds),
                                                             HandoffTask.status.in_((TaskStatus.OPEN, TaskStatus.ACCEPTED))))).scalars().all()
    for t in tasks:
        t.status, t.completed_at = final_status, datetime.now(timezone.utc)
        t.note = (t.note or reason)[:1000]
        _audit(session, tenant_id, None, "mt.task.cancelled" if final_status == TaskStatus.CANCELLED else "mt.task.completed", t, {"auto": True, "reason": reason})
    return len(tasks)
