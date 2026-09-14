import uuid

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.morning_brief import (
    MorningBrief,
    MorningBriefInsight,
    MorningBriefRecommendation,
    RecommendationStatus,
)
from app.models.rbac import Permission
from app.services.morning_brief_service import MorningBriefService
from app.tools.base import ExecutionContext, Tool


class EmptyInput(BaseModel):
    pass


class GenerateMorningBriefOutput(BaseModel):
    brief_id: str
    headline: str
    mode: str


class GenerateMorningBrief(Tool):
    """Human-triggered generation ("Generate now"). The scheduler
    (MorningBriefService.check_and_generate_scheduled, driven by the Event
    Worker's tick loop) calls MorningBriefService.generate directly instead
    of going through this tool, since there is no human ExecutionContext for
    a scheduled run — but it produces an identical MorningBrief row, just
    with generated_by=SYSTEM instead of USER."""

    name = "insights.generate_morning_brief"
    description = "Generate a Morning Brief for the caller's tenant from real, current data."
    input_schema = EmptyInput
    output_schema = GenerateMorningBriefOutput
    required_permission = Permission.GENERATE_MORNING_BRIEF
    counts_toward_ai_usage = True

    def __init__(self, morning_brief_service: MorningBriefService) -> None:
        self._morning_brief_service = morning_brief_service

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> GenerateMorningBriefOutput:
        result = await self._morning_brief_service.generate(context.tenant_id, generated_by=context.actor_type)
        return GenerateMorningBriefOutput(**result)


class InsightOut(BaseModel):
    insight_id: str
    category: str
    priority: str
    summary: str
    related_entity_type: str | None
    related_entity_id: str | None


class RecommendationOut(BaseModel):
    recommendation_id: str
    what: str
    why: str
    related_entity_type: str | None
    related_entity_id: str | None
    next_action: str
    executable: bool
    status: str
    approval_request_id: str | None = None


class GetLatestMorningBriefOutput(BaseModel):
    brief_id: str | None
    brief_date: str | None
    generated_at: str | None
    mode: str | None
    generated_by: str | None
    headline: str | None
    ai_provider: str | None = None
    ai_model: str | None = None
    insights: list[InsightOut]
    recommendations: list[RecommendationOut]


class GetLatestMorningBrief(Tool):
    name = "insights.get_latest_morning_brief"
    description = "Fetch the most recently generated Morning Brief for the caller's tenant, if any."
    input_schema = EmptyInput
    output_schema = GetLatestMorningBriefOutput
    required_permission = Permission.READ_MORNING_BRIEF

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(self, input: EmptyInput, context: ExecutionContext) -> GetLatestMorningBriefOutput:
        async with self._session_factory() as session:
            brief = (
                await session.execute(
                    select(MorningBrief)
                    .where(MorningBrief.tenant_id == context.tenant_id)
                    .order_by(MorningBrief.generated_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if brief is None:
                return GetLatestMorningBriefOutput(
                    brief_id=None,
                    brief_date=None,
                    generated_at=None,
                    mode=None,
                    generated_by=None,
                    headline=None,
                    ai_provider=None,
                    ai_model=None,
                    insights=[],
                    recommendations=[],
                )
            insight_rows = (
                await session.execute(
                    select(MorningBriefInsight).where(MorningBriefInsight.brief_id == brief.id)
                )
            ).scalars().all()
            rec_rows = (
                await session.execute(
                    select(MorningBriefRecommendation).where(MorningBriefRecommendation.brief_id == brief.id)
                )
            ).scalars().all()
            return GetLatestMorningBriefOutput(
                brief_id=str(brief.id),
                brief_date=brief.brief_date.isoformat(),
                generated_at=brief.generated_at.isoformat(),
                mode=brief.mode,
                generated_by=brief.generated_by,
                headline=brief.headline,
                ai_provider=brief.ai_provider,
                ai_model=brief.ai_model,
                insights=[
                    InsightOut(
                        insight_id=str(i.id),
                        category=i.category,
                        priority=i.priority,
                        summary=i.summary,
                        related_entity_type=i.related_entity_type,
                        related_entity_id=str(i.related_entity_id) if i.related_entity_id else None,
                    )
                    for i in insight_rows
                ],
                recommendations=[
                    RecommendationOut(
                        recommendation_id=str(r.id),
                        what=r.what,
                        why=r.why,
                        related_entity_type=r.related_entity_type,
                        related_entity_id=str(r.related_entity_id) if r.related_entity_id else None,
                        next_action=r.next_action,
                        executable=r.executable_tool is not None,
                        status=r.status,
                        approval_request_id=str(r.approval_request_id) if r.approval_request_id else None,
                    )
                    for r in rec_rows
                ],
            )


class ExecuteRecommendationInput(BaseModel):
    recommendation_id: uuid.UUID


class ExecuteRecommendationOutput(BaseModel):
    recommendation_id: str
    status: str
    tool_result: dict | None
    approval_request_id: str | None = None


class ExecuteRecommendation(Tool):
    """Turns a recommendation into a real action — but only ever by calling
    the specific `executable_tool` it was created with, through the normal
    ToolRegistry pipeline (permission -> policy -> execute -> audit) using
    THIS call's own ExecutionContext (the human clicking Execute, not the
    Morning Brief's AI context) — so an APPROVAL_REQUIRED or BLOCKED tool
    behaves exactly as it would if the user had called it directly. A
    recommendation with no `executable_tool` cannot be executed at all —
    it is informational only, and this raises rather than silently
    no-op'ing."""

    name = "insights.execute_recommendation"
    description = "Execute a Morning Brief recommendation's underlying tool call, if it has one."
    input_schema = ExecuteRecommendationInput
    output_schema = ExecuteRecommendationOutput
    required_permission = Permission.EXECUTE_RECOMMENDATION

    def __init__(self, session_factory: async_sessionmaker, registry) -> None:
        self._session_factory = session_factory
        self._registry = registry

    async def execute(
        self, input: ExecuteRecommendationInput, context: ExecutionContext
    ) -> ExecuteRecommendationOutput:
        from app.tools.errors import ToolApprovalRequiredError

        async with self._session_factory() as session:
            rec = await session.get(MorningBriefRecommendation, input.recommendation_id)
            if rec is None or rec.tenant_id != context.tenant_id:
                raise ValueError("Recommendation not found")
            if rec.status != RecommendationStatus.PENDING:
                raise ValueError(f"Recommendation already {rec.status}")
            if rec.executable_tool is None:
                raise ValueError("This recommendation has no executable action — it is informational only")
            tool_name = rec.executable_tool
            tool_input = rec.executable_input or {}

            # Phase 9: if the underlying tool requires approval, that is not
            # a failure — the recommendation moves to APPROVAL_REQUESTED and
            # links to the real ApprovalRequest ToolRegistry just created.
            # Approving it (via /approvals) resumes execution through
            # ApprovalExecutionService — never a second, bespoke resume path
            # here.
            try:
                result = await self._registry.execute(tool_name, tool_input, context)
            except ToolApprovalRequiredError as exc:
                rec.status = RecommendationStatus.APPROVAL_REQUESTED
                rec.approval_request_id = exc.approval_request_id
                await session.commit()
                return ExecuteRecommendationOutput(
                    recommendation_id=str(rec.id),
                    status=rec.status,
                    tool_result=None,
                    approval_request_id=str(exc.approval_request_id),
                )

            rec.status = RecommendationStatus.EXECUTED
            await session.commit()

            return ExecuteRecommendationOutput(
                recommendation_id=str(rec.id),
                status=rec.status,
                tool_result=result.model_dump(mode="json") if hasattr(result, "model_dump") else None,
            )


class DismissRecommendationInput(BaseModel):
    recommendation_id: uuid.UUID


class DismissRecommendationOutput(BaseModel):
    recommendation_id: str
    status: str


class DismissRecommendation(Tool):
    name = "insights.dismiss_recommendation"
    description = "Dismiss a Morning Brief recommendation without executing it."
    input_schema = DismissRecommendationInput
    output_schema = DismissRecommendationOutput
    required_permission = Permission.EXECUTE_RECOMMENDATION

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def execute(
        self, input: DismissRecommendationInput, context: ExecutionContext
    ) -> DismissRecommendationOutput:
        async with self._session_factory() as session:
            rec = await session.get(MorningBriefRecommendation, input.recommendation_id)
            if rec is None or rec.tenant_id != context.tenant_id:
                raise ValueError("Recommendation not found")
            rec.status = RecommendationStatus.DISMISSED
            await session.commit()
            return DismissRecommendationOutput(recommendation_id=str(rec.id), status=rec.status)
