from fastapi import APIRouter

from app.api.v1 import (
    appointments,
    approvals,
    auth,
    crm,
    customers,
    events,
    integrations,
    leads,
    tools,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(events.router)
api_router.include_router(tools.router)
api_router.include_router(approvals.router)
api_router.include_router(integrations.router)
api_router.include_router(leads.router)
api_router.include_router(customers.router)
api_router.include_router(appointments.router)
api_router.include_router(crm.router)
