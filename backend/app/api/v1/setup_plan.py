"""`/api/v1/setup-plan`: what it takes to run this business on Klaros, and one call that does everything Klaros can do on its own. Same for every business."""

from typing import Any

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from app.api.deps import CurrentUser, require_permission
from app.db.session import async_session_maker, set_tenant_context
from app.models.actor import ActorType
from app.models.audit_log import AuditLog
from app.models.user import User
from app.models.rbac import Permission
from app.services import setup_plan

router = APIRouter(prefix="/setup-plan", tags=["setup-plan"])


@router.get("")
async def get_setup_plan(current_user: CurrentUser = Depends(require_permission(Permission.READ_BUSINESS_JOURNEY))) -> dict[str, Any]:
    return await setup_plan.build_plan(async_session_maker, current_user.tenant_id)


@router.post("/activate")
async def activate_setup_plan(current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY))) -> dict[str, Any]:
    return await setup_plan.activate(async_session_maker, current_user.tenant_id, current_user.id)


TEST_EMAIL_ACTION = "setup.test_email"
TEST_EMAIL_PER_HOUR = 5


@router.post("/test-email")
async def send_test_email(current_user: CurrentUser = Depends(require_permission(Permission.MANAGE_BUSINESS_JOURNEY))) -> dict[str, Any]:
    """Send a short test message to the signed-in person's OWN address, so the owner can see that e-mail really works before anything real goes out. It never takes an address from the
    request, is limited to a few a hour, and reports honestly whether the provider accepted it."""
    from app.communications.email_config import email_configured
    from app.services.system_mailer import send_system_email

    if not email_configured():
        return {"sent": False, "reason": "No e-mail provider is configured on this deployment yet."}
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        user = await session.get(User, current_user.id)
        recent = (await session.execute(select(func.count()).select_from(AuditLog).where(
            AuditLog.tenant_id == current_user.tenant_id, AuditLog.action == TEST_EMAIL_ACTION, AuditLog.created_at >= datetime.now(timezone.utc) - timedelta(hours=1)))).scalar_one()
    if user is None or not user.email:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No e-mail address on your account.")
    if recent >= TEST_EMAIL_PER_HOUR:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too many test e-mails this hour. Try again later.")
    sent = await send_system_email(to=user.email, subject="Klaros e-mail test", body="This is a test message from Klaros. If you can read it, e-mail is working for your business.")
    async with async_session_maker() as session:
        await set_tenant_context(session, current_user.tenant_id)
        session.add(AuditLog(tenant_id=current_user.tenant_id, actor_type=ActorType.USER, actor_id=current_user.id, action=TEST_EMAIL_ACTION, entity_type="user", entity_id=current_user.id,
                             input_summary={}, result="success" if sent else "failure"))
        await session.commit()
    return {"sent": sent, "to": user.email if sent else None, "reason": None if sent else "The e-mail provider did not accept the message."}
