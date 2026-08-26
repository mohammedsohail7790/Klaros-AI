import base64
import uuid

import pytest

from app.models.actor import ActorType
from app.models.rbac import Role
from app.tools.base import ExecutionContext

pytestmark = pytest.mark.asyncio


def _ctx(tenant_id):
    return ExecutionContext(tenant_id=tenant_id, actor_type=ActorType.USER, actor_id=uuid.uuid4(), role=Role.OWNER)


async def _job(tool_registry, tenant_id) -> str:
    customer = await tool_registry.execute("crm.create_customer", {"name": "C"}, _ctx(tenant_id))
    job = await tool_registry.execute(
        "operations.create_job", {"title": "Job", "customer_id": customer.customer["id"]}, _ctx(tenant_id)
    )
    return job.job["id"]


async def test_create_and_complete_task(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    task = await tool_registry.execute(
        "operations.create_task", {"job_id": job_id, "title": "Inspect unit", "required": True}, _ctx(tenant_id)
    )
    assert task.task["status"] == "PENDING"

    completed = await tool_registry.execute(
        "operations.complete_task", {"task_id": task.task["id"]}, _ctx(tenant_id)
    )
    assert completed.task["status"] == "COMPLETED"
    assert completed.task["completed_at"] is not None


async def test_add_material_and_create_po_draft(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    await tool_registry.execute(
        "operations.add_material",
        {"job_id": job_id, "name": "Filter", "quantity": 3, "unit": "each", "estimated_unit_cost": 12.5},
        _ctx(tenant_id),
    )
    await tool_registry.execute(
        "operations.add_material", {"job_id": job_id, "name": "Valve", "quantity": 1}, _ctx(tenant_id)
    )

    po = await tool_registry.execute("operations.create_purchase_order_draft", {"job_id": job_id}, _ctx(tenant_id))
    assert po.purchase_order["status"] == "DRAFT"
    assert len(po.items) == 2


async def test_upload_document_real_bytes_on_disk(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    content = b"%PDF-1.4 fake receipt content"
    result = await tool_registry.execute(
        "operations.add_job_document",
        {
            "job_id": job_id,
            "filename": "receipt.pdf",
            "content_type": "application/pdf",
            "content_base64": base64.b64encode(content).decode(),
        },
        _ctx(tenant_id),
    )
    assert result.attachment["kind"] == "DOCUMENT"
    assert result.attachment["size_bytes"] == len(content)
    assert result.attachment["storage_provider"] == "internal_local_storage"


async def test_upload_photo(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    content = b"\xff\xd8\xff fake jpeg bytes"
    result = await tool_registry.execute(
        "operations.add_job_photo",
        {
            "job_id": job_id,
            "filename": "before.jpg",
            "content_type": "image/jpeg",
            "content_base64": base64.b64encode(content).decode(),
        },
        _ctx(tenant_id),
    )
    assert result.attachment["kind"] == "PHOTO"


async def test_upload_voice_note_transcription_not_configured(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    content = b"fake audio bytes"
    result = await tool_registry.execute(
        "operations.add_voice_note",
        {
            "job_id": job_id,
            "filename": "note.mp3",
            "content_type": "audio/mpeg",
            "content_base64": base64.b64encode(content).decode(),
            "duration_seconds": 42,
        },
        _ctx(tenant_id),
    )
    assert result.attachment["kind"] == "VOICE_NOTE"
    assert result.attachment["transcription_status"] == "NOT_CONFIGURED"
    assert result.attachment["duration_seconds"] == 42


async def test_upload_rejects_unsupported_content_type(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    with pytest.raises(ValueError, match="Unsupported"):
        await tool_registry.execute(
            "operations.add_job_document",
            {
                "job_id": job_id,
                "filename": "malware.exe",
                "content_type": "application/x-msdownload",
                "content_base64": base64.b64encode(b"whatever").decode(),
            },
            _ctx(tenant_id),
        )


async def test_upload_rejects_oversized_file(tool_registry) -> None:
    tenant_id = uuid.uuid4()
    job_id = await _job(tool_registry, tenant_id)

    big_content = b"a" * (25 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="exceeds"):
        await tool_registry.execute(
            "operations.add_job_document",
            {
                "job_id": job_id,
                "filename": "huge.pdf",
                "content_type": "application/pdf",
                "content_base64": base64.b64encode(big_content).decode(),
            },
            _ctx(tenant_id),
        )
