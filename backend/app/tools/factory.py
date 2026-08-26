from sqlalchemy.ext.asyncio import async_sessionmaker

from app.calendar.internal_test_adapter import InternalTestCalendarAdapter
from app.events.bus import EventBus
from app.services.attachment_service import AttachmentService
from app.services.completion_service import CompletionService
from app.services.conversion_service import LeadConversionService
from app.services.enrichment_service import LeadEnrichmentService
from app.services.exception_service import ExceptionService
from app.services.job_service import JobService
from app.services.job_transition_service import JobTransitionService
from app.services.lead_service import LeadService
from app.services.material_service import MaterialService
from app.services.qa_service import QAService
from app.services.qualification_service import LeadQualificationService
from app.services.scope_change_service import ScopeChangeService
from app.services.signoff_service import InternalCustomerSignoffProvider
from app.services.task_service import TaskService
from app.storage.factory import get_object_storage
from app.tools.builtin.approval_tools import CreateApprovalRequest
from app.tools.builtin.appointment_tools import (
    CancelAppointment,
    CheckAvailability,
    CreateAppointment,
    RescheduleAppointment,
)
from app.tools.builtin.audit_tools import RecordAction
from app.tools.builtin.completion_tools import CloseJob, GenerateCompletionPacket, RecordCustomerSignoff
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
from app.tools.builtin.document_tools import AddJobDocument, AddJobPhoto, AddVoiceNote
from app.tools.builtin.event_tools import GetEvent, PublishEvent
from app.tools.builtin.exception_tools import CreateException, ResolveException
from app.tools.builtin.job_summary_tool import GenerateJobSummary
from app.tools.builtin.job_tools import (
    AssignJob,
    BlockJob,
    CancelJob,
    CompleteJob,
    ConvertLeadAndBook,
    CreateJob,
    DispatchJob,
    GetJob,
    GetJobTimeline,
    RescheduleJob,
    ScheduleJob,
    SearchJobs,
    StartJob,
    UnassignJob,
    UnblockJob,
    UpdateJob,
    UpdateJobStatus,
)
from app.tools.builtin.notification_tools import CreateNotification
from app.tools.builtin.qa_tools import CompleteQA, FailQA, StartQA
from app.tools.builtin.scope_change_tools import CreateScopeChange, RequestScopeChangeApproval
from app.tools.builtin.system_tools import GetCurrentTime, GetTenantContext
from app.tools.builtin.task_material_tools import (
    AddMaterial,
    CompleteTask,
    CreatePurchaseOrderDraft,
    CreateTask,
)
from app.tools.builtin.worker_tools import CreateWorker, ListWorkers, UpdateWorkerStatus
from app.tools.registry import ToolRegistry


def build_tool_registry(session_factory: async_sessionmaker, bus: EventBus) -> ToolRegistry:
    registry = ToolRegistry(session_factory)

    enrichment = LeadEnrichmentService(session_factory)
    lead_service = LeadService(session_factory, bus)
    qualification_service = LeadQualificationService(session_factory, bus, enrichment)
    calendar = InternalTestCalendarAdapter(session_factory)

    job_service = JobService(session_factory, bus)
    job_transitions = JobTransitionService(session_factory, bus)
    task_service = TaskService(session_factory)
    material_service = MaterialService(session_factory)
    attachment_service = AttachmentService(session_factory, get_object_storage())
    qa_service = QAService(session_factory, bus)
    exception_service = ExceptionService(session_factory, bus)
    scope_change_service = ScopeChangeService(session_factory, bus)
    completion_service = CompletionService(session_factory, bus)
    signoff_provider = InternalCustomerSignoffProvider(session_factory)
    conversion_service = LeadConversionService(session_factory, bus, calendar, job_service)

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

    registry.register(CreateJob(job_service))
    registry.register(GetJob(session_factory))
    registry.register(UpdateJob(session_factory))
    registry.register(SearchJobs(session_factory))
    registry.register(AssignJob(job_transitions))
    registry.register(UnassignJob(job_transitions))
    registry.register(ScheduleJob(job_transitions))
    registry.register(RescheduleJob(job_transitions))
    registry.register(DispatchJob(job_transitions))
    registry.register(UpdateJobStatus(job_transitions))
    registry.register(StartJob(job_transitions))
    registry.register(CompleteJob(job_transitions))
    registry.register(CancelJob(job_transitions))
    registry.register(BlockJob(job_transitions))
    registry.register(UnblockJob(job_transitions))
    registry.register(GetJobTimeline(session_factory))
    registry.register(GenerateJobSummary(session_factory))
    registry.register(ConvertLeadAndBook(conversion_service))

    registry.register(CreateWorker(session_factory))
    registry.register(ListWorkers(session_factory))
    registry.register(UpdateWorkerStatus(session_factory))

    registry.register(CreateTask(task_service))
    registry.register(CompleteTask(task_service))
    registry.register(AddMaterial(material_service))
    registry.register(CreatePurchaseOrderDraft(material_service))

    registry.register(AddJobDocument(attachment_service))
    registry.register(AddJobPhoto(attachment_service))
    registry.register(AddVoiceNote(attachment_service))

    registry.register(StartQA(qa_service))
    registry.register(CompleteQA(qa_service))
    registry.register(FailQA(qa_service))

    registry.register(CreateScopeChange(scope_change_service))
    registry.register(RequestScopeChangeApproval())

    registry.register(CreateException(exception_service))
    registry.register(ResolveException(exception_service))

    registry.register(GenerateCompletionPacket(completion_service))
    registry.register(CloseJob(completion_service))
    registry.register(RecordCustomerSignoff(signoff_provider))

    return registry
