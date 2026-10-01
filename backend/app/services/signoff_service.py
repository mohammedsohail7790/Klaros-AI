"""section 27: InternalCustomerSignoffProvider.

Records that a customer acknowledged job completion — an internal record,
not a legally binding e-signature. `signature_reference` is an opaque
internal token, never presented as cryptographic proof. A real e-signature
provider (DocuSign etc.) is a future adapter behind the same shape.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db.session import set_tenant_context
from app.models.operations import CustomerSignoff, Job


class JobNotFoundError(Exception):
    pass


class InternalCustomerSignoffProvider:
    provider_name = "internal_signoff"

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def record_signoff(self, tenant_id: uuid.UUID, job_id: uuid.UUID, *, signed_by: str) -> CustomerSignoff:
        async with self._session_factory() as session:
            await set_tenant_context(session, tenant_id)
            job = await session.get(Job, job_id)
            if job is None or job.tenant_id != tenant_id:
                raise JobNotFoundError("Job not found")

            signoff = CustomerSignoff(
                tenant_id=tenant_id,
                job_id=job_id,
                customer_id=job.customer_id,
                signed_at=datetime.now(timezone.utc),
                signed_by=signed_by,
                signature_reference=str(uuid.uuid4()),
                provider=self.provider_name,
            )
            session.add(signoff)
            await session.commit()
            await session.refresh(signoff)
            return signoff
