"""section 16-18: field documentation — documents, photos, voice notes.

Real upload/storage: every file actually goes through `ObjectStorageProvider`
(`app/storage/`) and is only recorded in `job_attachments` after the bytes
are durably written — never faked. Voice-note transcription is explicitly
`NOT_CONFIGURED` (no transcription provider exists yet); the architecture
(a `transcription_status`/`transcript` pair) is ready for one to be plugged
in later without a schema change.
"""

import uuid

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.operations import AttachmentKind, Job, JobAttachment, TranscriptionStatus
from app.storage.base import ObjectStorageProvider


class JobNotFoundError(Exception):
    pass


class AttachmentService:
    def __init__(self, session_factory: async_sessionmaker, storage: ObjectStorageProvider) -> None:
        self._session_factory = session_factory
        self._storage = storage

    async def _get_job(self, session, tenant_id: uuid.UUID, job_id: uuid.UUID) -> Job:
        job = await session.get(Job, job_id)
        if job is None or job.tenant_id != tenant_id:
            raise JobNotFoundError("Job not found")
        return job

    async def add_document(
        self,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        kind: AttachmentKind,
        filename: str,
        content: bytes,
        content_type: str,
        uploaded_by: uuid.UUID | None,
        note: str | None = None,
        duration_seconds: int | None = None,
    ) -> JobAttachment:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            await self._get_job(session, tenant_id, job_id)

            stored = await self._storage.put(tenant_id, filename, content, content_type)

            attachment = JobAttachment(
                tenant_id=tenant_id,
                job_id=job_id,
                kind=kind,
                filename=filename,
                content_type=content_type,
                size_bytes=stored.size_bytes,
                storage_key=stored.storage_key,
                storage_provider=stored.provider,
                uploaded_by=uploaded_by,
                note=note,
                duration_seconds=duration_seconds if kind == AttachmentKind.VOICE_NOTE else None,
                transcription_status=(
                    TranscriptionStatus.NOT_CONFIGURED if kind == AttachmentKind.VOICE_NOTE else None
                ),
            )
            session.add(attachment)
            await session.commit()
            await session.refresh(attachment)
            return attachment
