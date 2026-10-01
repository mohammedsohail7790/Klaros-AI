"""section 19/20: materials and the purchase-order-draft foundation.

No supplier integration exists (`ProcurementProvider` is NOT_CONNECTED —
see app/integrations/adapters.py's addition in this phase). A generated PO
always starts and stays `DRAFT` in Phase 4 — nothing here sends anything to
a real supplier.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.operations import (
    Job,
    JobMaterial,
    MaterialStatus,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseOrderStatus,
)


class JobNotFoundError(Exception):
    pass


class MaterialService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def add_material(
        self,
        tenant_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        name: str,
        quantity: float,
        unit: str | None = None,
        description: str | None = None,
        estimated_unit_cost: float | None = None,
        supplier: str | None = None,
    ) -> JobMaterial:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            material = JobMaterial(
                tenant_id=tenant_id,
                job_id=job_id,
                name=name,
                description=description,
                quantity=quantity,
                unit=unit,
                estimated_unit_cost=estimated_unit_cost,
                supplier=supplier,
                status=MaterialStatus.REQUIRED,
            )
            session.add(material)
            await session.commit()
            await session.refresh(material)
            return material

    async def create_purchase_order_draft(
        self, tenant_id: uuid.UUID, job_id: uuid.UUID, *, supplier: str | None = None
    ) -> tuple[PurchaseOrder, list[PurchaseOrderItem]]:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            materials = (
                await session.execute(
                    select(JobMaterial).where(
                        JobMaterial.tenant_id == tenant_id,
                        JobMaterial.job_id == job_id,
                        JobMaterial.status == MaterialStatus.REQUIRED,
                    )
                )
            ).scalars().all()

            po = PurchaseOrder(
                tenant_id=tenant_id,
                job_id=job_id,
                supplier=supplier,
                status=PurchaseOrderStatus.DRAFT,
                provider="internal_draft",
            )
            session.add(po)
            await session.flush()

            items = []
            for material in materials:
                item = PurchaseOrderItem(
                    tenant_id=tenant_id,
                    purchase_order_id=po.id,
                    job_material_id=material.id,
                    name=material.name,
                    quantity=material.quantity,
                    unit=material.unit,
                    estimated_unit_cost=material.estimated_unit_cost,
                )
                session.add(item)
                items.append(item)
                material.status = MaterialStatus.REQUESTED

            await session.commit()
            await session.refresh(po)
            for item in items:
                await session.refresh(item)
            return po, items
