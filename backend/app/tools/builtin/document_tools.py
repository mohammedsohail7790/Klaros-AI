"""section 16-18: field documentation tools.

Tool inputs are JSON, so file bytes travel as base64 (`content_base64`) —
the API layer (multipart upload) decodes into this shape; nothing here
accepts a raw filesystem path or trusts client-supplied storage paths.
"""

import base64
import uuid
from typing import Any

from pydantic import BaseModel

from app.models.operations import AttachmentKind, JobAttachment
from app.models.rbac import Permission
from app.services.attachment_service import AttachmentService, JobNotFoundError
from app.storage.base import NotConnectedObjectStorageError
from app.storage.local_adapter import UnsupportedFileError
from app.tools.base import ExecutionContext, Tool


def _attachment_to_dict(a: JobAttachment) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "job_id": str(a.job_id),
        "kind": a.kind,
        "filename": a.filename,
        "content_type": a.content_type,
        "size_bytes": a.size_bytes,
        "storage_provider": a.storage_provider,
        "duration_seconds": a.duration_seconds,
        "transcription_status": a.transcription_status,
    }


def _decode(content_base64: str) -> bytes:
    try:
        return base64.b64decode(content_base64)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Invalid base64 content: {exc}") from exc


class AddAttachmentInput(BaseModel):
    job_id: uuid.UUID
    filename: str
    content_type: str
    content_base64: str
    note: str | None = None


class AttachmentOutput(BaseModel):
    attachment: dict[str, Any]


class AddJobDocument(Tool):
    name = "operations.add_job_document"
    description = "Upload a document (PDF, receipt, text) attached to a job."
    input_schema = AddAttachmentInput
    output_schema = AttachmentOutput
    required_permission = Permission.UPLOAD_JOB_ATTACHMENT

    def __init__(self, attachment_service: AttachmentService) -> None:
        self._attachment_service = attachment_service

    async def execute(self, input: AddAttachmentInput, context: ExecutionContext) -> AttachmentOutput:
        try:
            attachment = await self._attachment_service.add_document(
                context.tenant_id,
                input.job_id,
                kind=AttachmentKind.DOCUMENT,
                filename=input.filename,
                content=_decode(input.content_base64),
                content_type=input.content_type,
                uploaded_by=context.actor_id,
                note=input.note,
            )
        except (UnsupportedFileError, NotConnectedObjectStorageError, JobNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return AttachmentOutput(attachment=_attachment_to_dict(attachment))


class AddJobPhoto(Tool):
    name = "operations.add_job_photo"
    description = "Upload a photo attached to a job."
    input_schema = AddAttachmentInput
    output_schema = AttachmentOutput
    required_permission = Permission.UPLOAD_JOB_ATTACHMENT

    def __init__(self, attachment_service: AttachmentService) -> None:
        self._attachment_service = attachment_service

    async def execute(self, input: AddAttachmentInput, context: ExecutionContext) -> AttachmentOutput:
        try:
            attachment = await self._attachment_service.add_document(
                context.tenant_id,
                input.job_id,
                kind=AttachmentKind.PHOTO,
                filename=input.filename,
                content=_decode(input.content_base64),
                content_type=input.content_type,
                uploaded_by=context.actor_id,
                note=input.note,
            )
        except (UnsupportedFileError, NotConnectedObjectStorageError, JobNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return AttachmentOutput(attachment=_attachment_to_dict(attachment))


class AddVoiceNoteInput(AddAttachmentInput):
    duration_seconds: int | None = None


class AddVoiceNote(Tool):
    name = "operations.add_voice_note"
    description = "Upload a voice note attached to a job. Transcription is NOT_CONFIGURED — no provider connected."
    input_schema = AddVoiceNoteInput
    output_schema = AttachmentOutput
    required_permission = Permission.UPLOAD_JOB_ATTACHMENT

    def __init__(self, attachment_service: AttachmentService) -> None:
        self._attachment_service = attachment_service

    async def execute(self, input: AddVoiceNoteInput, context: ExecutionContext) -> AttachmentOutput:
        try:
            attachment = await self._attachment_service.add_document(
                context.tenant_id,
                input.job_id,
                kind=AttachmentKind.VOICE_NOTE,
                filename=input.filename,
                content=_decode(input.content_base64),
                content_type=input.content_type,
                uploaded_by=context.actor_id,
                note=input.note,
                duration_seconds=input.duration_seconds,
            )
        except (UnsupportedFileError, NotConnectedObjectStorageError, JobNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return AttachmentOutput(attachment=_attachment_to_dict(attachment))
