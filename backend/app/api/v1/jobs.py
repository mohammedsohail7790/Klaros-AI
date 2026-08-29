import base64
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import CurrentUser, get_current_user
from app.api.tool_deps import execution_context, get_tool_registry, raise_http_for_tool_error
from app.db.session import async_session_maker
from app.models.operations import Job, JobAttachment, JobMaterial, JobTask
from app.storage.factory import get_object_storage
from app.tools.errors import ToolError
from app.tools.registry import ToolRegistry

router = APIRouter(prefix="/jobs", tags=["jobs"])


async def _run(registry: ToolRegistry, tool_name: str, payload: dict, current_user: CurrentUser) -> dict[str, Any]:
    try:
        output = await registry.execute(tool_name, payload, execution_context(current_user))
    except (ToolError, ValueError) as exc:
        raise_http_for_tool_error(exc)
    return output.model_dump(mode="json")


class CreateJobRequest(BaseModel):
    title: str
    customer_id: uuid.UUID
    lead_id: uuid.UUID | None = None
    appointment_id: uuid.UUID | None = None
    description: str | None = None
    service_type: str | None = None
    priority: str = "NORMAL"
    location: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    estimated_duration_minutes: int | None = None
    estimated_revenue: float | None = None
    estimated_cost: float | None = None
    idempotency_key: str | None = None


@router.post("", status_code=201)
async def create_job(
    body: CreateJobRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.create_job", body.model_dump(mode="json"), current_user)


class ConvertLeadRequest(BaseModel):
    lead_id: uuid.UUID
    title: str
    start_time: datetime
    end_time: datetime
    assigned_user_id: uuid.UUID | None = None
    idempotency_key: str | None = None


@router.post("/convert-lead", status_code=201)
async def convert_lead_and_book(
    body: ConvertLeadRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    """section 4: the explicit lead -> customer -> appointment -> job chain."""
    return await _run(registry, "operations.convert_lead_and_book", body.model_dump(mode="json"), current_user)


@router.get("")
async def search_jobs(
    status: str | None = None,
    priority: str | None = None,
    assigned_user_id: uuid.UUID | None = None,
    q: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {
        "status": status,
        "priority": priority,
        "assigned_user_id": str(assigned_user_id) if assigned_user_id else None,
        "q": q,
        "limit": limit,
        "offset": offset,
    }
    return await _run(registry, "operations.search_jobs", payload, current_user)


@router.get("/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.get_job", {"job_id": str(job_id)}, current_user)


class UpdateJobRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    priority: str | None = None
    customer_notes: str | None = None
    internal_notes: str | None = None


@router.patch("/{job_id}")
async def update_job(
    job_id: uuid.UUID,
    body: UpdateJobRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump()}
    return await _run(registry, "operations.update_job", payload, current_user)


@router.get("/{job_id}/timeline")
async def get_job_timeline(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.get_job_timeline", {"job_id": str(job_id)}, current_user)


@router.get("/{job_id}/summary")
async def get_job_summary(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.generate_job_summary", {"job_id": str(job_id)}, current_user)


async def _owned_job_or_404(job_id: uuid.UUID, current_user: CurrentUser) -> None:
    from fastapi import HTTPException, status

    async with async_session_maker() as session:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != current_user.tenant_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")


@router.get("/{job_id}/tasks")
async def list_job_tasks(
    job_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    """Read-only listing — no tool exists for "list tasks" specifically, so
    (like GET /crm/metrics and GET /operations/dashboard) this reads
    directly rather than through a tool."""
    await _owned_job_or_404(job_id, current_user)
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(JobTask)
                .where(JobTask.tenant_id == current_user.tenant_id, JobTask.job_id == job_id)
                .order_by(JobTask.sort_order)
            )
        ).scalars().all()
    return {
        "tasks": [
            {
                "id": str(t.id),
                "job_id": str(t.job_id),
                "title": t.title,
                "description": t.description,
                "status": t.status,
                "required": t.required,
                "completed_at": t.completed_at.isoformat() if t.completed_at else None,
            }
            for t in rows
        ]
    }


@router.get("/{job_id}/materials")
async def list_job_materials(
    job_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    await _owned_job_or_404(job_id, current_user)
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(JobMaterial).where(
                    JobMaterial.tenant_id == current_user.tenant_id, JobMaterial.job_id == job_id
                )
            )
        ).scalars().all()
    return {
        "materials": [
            {
                "id": str(m.id),
                "job_id": str(m.job_id),
                "name": m.name,
                "quantity": float(m.quantity),
                "unit": m.unit,
                "status": m.status,
            }
            for m in rows
        ]
    }


@router.get("/{job_id}/attachments")
async def list_job_attachments(
    job_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> dict[str, Any]:
    await _owned_job_or_404(job_id, current_user)
    async with async_session_maker() as session:
        rows = (
            await session.execute(
                select(JobAttachment).where(
                    JobAttachment.tenant_id == current_user.tenant_id, JobAttachment.job_id == job_id
                )
            )
        ).scalars().all()
    return {
        "attachments": [
            {
                "id": str(a.id),
                "job_id": str(a.job_id),
                "kind": a.kind,
                "filename": a.filename,
                "content_type": a.content_type,
                "size_bytes": a.size_bytes,
                "storage_provider": a.storage_provider,
                "transcription_status": a.transcription_status,
            }
            for a in rows
        ]
    }


@router.get("/{job_id}/attachments/{attachment_id}/download")
async def download_job_attachment(
    job_id: uuid.UUID, attachment_id: uuid.UUID, current_user: CurrentUser = Depends(get_current_user)
) -> Response:
    """Phase 12 production hardening: attachments could be uploaded and
    listed as metadata since Phase 4, but never actually retrieved — a
    real, previously-unnoticed gap found in the Phase 11 production audit.
    Tenant ownership is re-verified from the DB row itself, never trusted
    from the URL alone; `LocalFilesystemStorageAdapter.get()` additionally
    scopes every read to `tenant_id` at the filesystem-key level, so even a
    guessed/leaked storage_key from another tenant can't be read through
    this path either."""
    await _owned_job_or_404(job_id, current_user)
    async with async_session_maker() as session:
        attachment = await session.get(JobAttachment, attachment_id)
    if (
        attachment is None
        or attachment.tenant_id != current_user.tenant_id
        or attachment.job_id != job_id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    storage = get_object_storage()
    try:
        content = await storage.get(current_user.tenant_id, attachment.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file not found") from exc

    return Response(
        content=content,
        media_type=attachment.content_type,
        headers={"Content-Disposition": f'inline; filename="{attachment.filename}"'},
    )


# --- Assignment & scheduling ---


class AssignRequest(BaseModel):
    worker_id: uuid.UUID


@router.post("/{job_id}/assign")
async def assign_job(
    job_id: uuid.UUID,
    body: AssignRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "worker_id": str(body.worker_id)}
    return await _run(registry, "operations.assign_job", payload, current_user)


@router.post("/{job_id}/unassign")
async def unassign_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.unassign_job", {"job_id": str(job_id)}, current_user)


class ScheduleRequest(BaseModel):
    start_time: datetime
    end_time: datetime


@router.post("/{job_id}/schedule")
async def schedule_job(
    job_id: uuid.UUID,
    body: ScheduleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump(mode="json")}
    return await _run(registry, "operations.schedule_job", payload, current_user)


@router.post("/{job_id}/reschedule")
async def reschedule_job(
    job_id: uuid.UUID,
    body: ScheduleRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump(mode="json")}
    return await _run(registry, "operations.reschedule_job", payload, current_user)


# --- State transitions ---


@router.post("/{job_id}/dispatch")
async def dispatch_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.dispatch_job", {"job_id": str(job_id)}, current_user)


class TransitionRequest(BaseModel):
    target_status: str


@router.post("/{job_id}/transition")
async def transition_job(
    job_id: uuid.UUID,
    body: TransitionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "target_status": body.target_status}
    return await _run(registry, "operations.update_job_status", payload, current_user)


@router.post("/{job_id}/start")
async def start_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.start_job", {"job_id": str(job_id)}, current_user)


@router.post("/{job_id}/complete")
async def complete_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.complete_job", {"job_id": str(job_id)}, current_user)


@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.cancel_job", {"job_id": str(job_id)}, current_user)


class BlockRequest(BaseModel):
    reason: str


@router.post("/{job_id}/block")
async def block_job(
    job_id: uuid.UUID,
    body: BlockRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "reason": body.reason}
    return await _run(registry, "operations.block_job", payload, current_user)


class UnblockRequest(BaseModel):
    target_status: str = "IN_PROGRESS"


@router.post("/{job_id}/unblock")
async def unblock_job(
    job_id: uuid.UUID,
    body: UnblockRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "target_status": body.target_status}
    return await _run(registry, "operations.unblock_job", payload, current_user)


# --- Tasks ---


class CreateTaskRequest(BaseModel):
    title: str
    description: str | None = None
    required: bool = True
    assigned_to: uuid.UUID | None = None


@router.post("/{job_id}/tasks", status_code=201)
async def create_task(
    job_id: uuid.UUID,
    body: CreateTaskRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump(mode="json")}
    return await _run(registry, "operations.create_task", payload, current_user)


class CompleteTaskRequest(BaseModel):
    skip: bool = False


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: uuid.UUID,
    body: CompleteTaskRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"task_id": str(task_id), "skip": body.skip}
    return await _run(registry, "operations.complete_task", payload, current_user)


# --- Materials / procurement ---


class AddMaterialRequest(BaseModel):
    name: str
    quantity: float = 1
    unit: str | None = None
    description: str | None = None
    estimated_unit_cost: float | None = None
    supplier: str | None = None


@router.post("/{job_id}/materials", status_code=201)
async def add_material(
    job_id: uuid.UUID,
    body: AddMaterialRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump()}
    return await _run(registry, "operations.add_material", payload, current_user)


class CreatePODraftRequest(BaseModel):
    supplier: str | None = None


@router.post("/{job_id}/purchase-order-draft", status_code=201)
async def create_po_draft(
    job_id: uuid.UUID,
    body: CreatePODraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "supplier": body.supplier}
    return await _run(registry, "operations.create_purchase_order_draft", payload, current_user)


# --- Documents / photos / voice notes ---


async def _upload(
    registry: ToolRegistry,
    tool_name: str,
    job_id: uuid.UUID,
    file: UploadFile,
    note: str | None,
    current_user: CurrentUser,
    extra: dict | None = None,
) -> dict[str, Any]:
    content = await file.read()
    payload = {
        "job_id": str(job_id),
        "filename": file.filename or "upload",
        "content_type": file.content_type or "application/octet-stream",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "note": note,
        **(extra or {}),
    }
    return await _run(registry, tool_name, payload, current_user)


@router.post("/{job_id}/documents", status_code=201)
async def add_job_document(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    note: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _upload(registry, "operations.add_job_document", job_id, file, note, current_user)


@router.post("/{job_id}/photos", status_code=201)
async def add_job_photo(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    note: str | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _upload(registry, "operations.add_job_photo", job_id, file, note, current_user)


@router.post("/{job_id}/voice-notes", status_code=201)
async def add_voice_note(
    job_id: uuid.UUID,
    file: UploadFile = File(...),
    note: str | None = Form(default=None),
    duration_seconds: int | None = Form(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _upload(
        registry,
        "operations.add_voice_note",
        job_id,
        file,
        note,
        current_user,
        extra={"duration_seconds": duration_seconds},
    )


# --- QA ---


@router.post("/{job_id}/qa/start")
async def start_qa(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.start_qa", {"job_id": str(job_id)}, current_user)


@router.post("/{job_id}/qa/complete")
async def complete_qa(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.complete_qa", {"job_id": str(job_id)}, current_user)


class FailQARequest(BaseModel):
    reason: str


@router.post("/{job_id}/qa/fail")
async def fail_qa(
    job_id: uuid.UUID,
    body: FailQARequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "reason": body.reason}
    return await _run(registry, "operations.fail_qa", payload, current_user)


# --- Scope changes ---


class CreateScopeChangeRequest(BaseModel):
    description: str
    reason: str | None = None
    estimated_cost: float | None = None
    estimated_revenue: float | None = None


@router.post("/{job_id}/scope-changes", status_code=201)
async def create_scope_change(
    job_id: uuid.UUID,
    body: CreateScopeChangeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), **body.model_dump()}
    return await _run(registry, "operations.create_scope_change", payload, current_user)


class RequestScopeApprovalRequest(BaseModel):
    justification: str


@router.post("/scope-changes/{scope_change_id}/request-approval")
async def request_scope_change_approval(
    scope_change_id: uuid.UUID,
    body: RequestScopeApprovalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"scope_change_id": str(scope_change_id), "justification": body.justification}
    return await _run(registry, "operations.request_scope_change_approval", payload, current_user)


# --- Completion / close-out ---


@router.post("/{job_id}/completion-packet")
async def generate_completion_packet(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.generate_completion_packet", {"job_id": str(job_id)}, current_user)


@router.post("/{job_id}/close")
async def close_job(
    job_id: uuid.UUID,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    return await _run(registry, "operations.close_job", {"job_id": str(job_id)}, current_user)


class SignoffRequest(BaseModel):
    signed_by: str


@router.post("/{job_id}/signoff", status_code=201)
async def record_signoff(
    job_id: uuid.UUID,
    body: SignoffRequest,
    current_user: CurrentUser = Depends(get_current_user),
    registry: ToolRegistry = Depends(get_tool_registry),
) -> dict[str, Any]:
    payload = {"job_id": str(job_id), "signed_by": body.signed_by}
    return await _run(registry, "operations.record_customer_signoff", payload, current_user)
