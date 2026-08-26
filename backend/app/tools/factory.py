from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.events.bus import EventBus
from app.services.enrichment_service import LeadEnrichmentService
from app.services.lead_service import LeadService
from app.services.qualification_service import LeadQualificationService
from app.tools.builtin.approval_tools import CreateApprovalRequest
from app.tools.builtin.appointment_tools import (
    CancelAppointment,
    CheckAvailability,
    CreateAppointment,
    RescheduleAppointment,
)
from app.tools.builtin.audit_tools import RecordAction
from app.tools.builtin.crm_tools import (
    CreateCustomer,
    CreateLead,
    CreateNote,
    GenerateCustomerSummary,
    GetCustomer,
    GetCustomerTimeline,
    GetLead,
    QualifyLead,
    SearchCustomers,
    SearchLeads,
    UpdateCustomer,
    UpdateLead,
)
from app.tools.builtin.event_tools import GetEvent, PublishEvent
from app.tools.builtin.notification_tools import CreateNotification
from app.tools.builtin.system_tools import GetCurrentTime, GetTenantContext
from app.tools.registry import ToolRegistry


def build_tool_registry(session_factory: async_sessionmaker, bus: EventBus) -> ToolRegistry:
    registry = ToolRegistry(session_factory)

    enrichment = LeadEnrichmentService(session_factory)
    lead_service = LeadService(session_factory, bus)
    qualification_service = LeadQualificationService(session_factory, bus, enrichment)
    calendar = InternalTestCalendarAdapter(session_factory)

    registry.register(GetTenantContext())
    registry.register(GetCurrentTime())
    registry.register(PublishEvent(bus))
    registry.register(GetEvent(session_factory))
    registry.register(CreateApprovalRequest(session_factory))
    registry.register(CreateNotification(session_factory))
    registry.register(RecordAction(session_factory))

    registry.register(CreateLead(lead_service))
    registry.register(GetLead(session_factory))
    registry.register(UpdateLead(session_factory))
    registry.register(SearchLeads(session_factory))
    registry.register(QualifyLead(qualification_service))
    registry.register(CreateCustomer(session_factory))
    registry.register(GetCustomer(session_factory))
    registry.register(UpdateCustomer(session_factory))
    registry.register(SearchCustomers(session_factory))
    registry.register(GetCustomerTimeline(session_factory))
    registry.register(CreateNote(session_factory))
    registry.register(GenerateCustomerSummary(session_factory))

    registry.register(CheckAvailability(calendar))
    registry.register(CreateAppointment(calendar, bus))
    registry.register(CancelAppointment(calendar, bus))
    registry.register(RescheduleAppointment(calendar, bus))

    return registry
