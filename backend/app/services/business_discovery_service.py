"""Phase 2 (KLAROS_BUSINESS_DISCOVERY_SPEC.md): the Business Discovery
orchestration service — session lifecycle, turn persistence, extraction
pipeline, and gap-driven adaptive follow-up questions.

Pipeline per KLAROS_BUSINESS_DISCOVERY_SPEC.md §3:
  free-text -> extraction pass -> draft BlueprintClaim rows (PROPOSED) ->
  gap check against MINIMUM_BAR_SECTIONS -> next question or COMPLETED.

Never writes to any operational table (CRM/Finance/etc.) — its only
outputs are DiscoverySession/DiscoveryTurn rows and BlueprintClaim/
BlueprintSection rows via BusinessBlueprintService. If AI extraction fails,
the turn's raw answer is still persisted and the session stays ACTIVE with
`extraction_error` recorded — never silently corrupted or lost.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.business_blueprint import MINIMUM_BAR_SECTIONS, BlueprintSectionStatus
from app.models.business_discovery import (
    DiscoverySession,
    DiscoverySessionStatus,
    DiscoveryTurn,
    DiscoveryTurnKind,
)
from app.services.business_blueprint_service import BusinessBlueprintService
from app.services.discovery_extraction_service import DiscoveryExtractionService


class DiscoverySessionNotFoundError(Exception):
    pass


class DiscoverySessionCompletedError(Exception):
    pass


@dataclass
class DiscoveryTurnResult:
    session: DiscoverySession
    turn: DiscoveryTurn
    proposed_claim_ids: list[uuid.UUID]
    next_question: str | None
    extraction_available: bool
    extraction_error: str | None = None


class BusinessDiscoveryService:
    def __init__(
        self,
        session_factory: async_sessionmaker,
        blueprint_service: BusinessBlueprintService,
        extraction_service: DiscoveryExtractionService,
    ) -> None:
        self._session_factory = session_factory
        self._blueprint_service = blueprint_service
        self._extraction_service = extraction_service

    async def _gap_keys(self, tenant_id: uuid.UUID, blueprint_id: uuid.UUID) -> list[str]:
        sections = await self._blueprint_service.list_sections(tenant_id, blueprint_id)
        by_key = {s.section_key: s for s in sections}
        return [
            key.value
            for key in MINIMUM_BAR_SECTIONS
            if by_key.get(key.value) is None or by_key[key.value].status != BlueprintSectionStatus.COMPLETE
        ]

    async def start_session(
        self, tenant_id: uuid.UUID, *, business_idea: str, created_by: uuid.UUID | None
    ) -> DiscoveryTurnResult:
        blueprint = await self._blueprint_service.get_or_create_draft(tenant_id, created_by=created_by)

        async with self._session_factory() as session:
            discovery_session = DiscoverySession(
                tenant_id=tenant_id,
                blueprint_id=blueprint.id,
                status=DiscoverySessionStatus.ACTIVE,
                business_idea=business_idea,
                questions_asked=0,
                created_by=created_by,
            )
            session.add(discovery_session)
            await session.flush()

            turn = DiscoveryTurn(
                tenant_id=tenant_id,
                discovery_session_id=discovery_session.id,
                sequence=0,
                kind=DiscoveryTurnKind.INITIAL_DESCRIPTION,
                question=None,
                answer=business_idea,
            )
            session.add(turn)
            await session.commit()
            await session.refresh(discovery_session)
            await session.refresh(turn)

        return await self._process_turn(discovery_session.id, turn, blueprint.id, is_initial=True, actor_id=created_by)

    async def submit_answer(
        self, tenant_id: uuid.UUID, discovery_session_id: uuid.UUID, *, answer: str, actor_id: uuid.UUID | None
    ) -> DiscoveryTurnResult:
        async with self._session_factory() as session:
            discovery_session = await session.get(DiscoverySession, discovery_session_id)
            if discovery_session is None or discovery_session.tenant_id != tenant_id:
                raise DiscoverySessionNotFoundError(f"DiscoverySession {discovery_session_id} not found")
            if discovery_session.status != DiscoverySessionStatus.ACTIVE:
                raise DiscoverySessionCompletedError(
                    f"DiscoverySession is {discovery_session.status}, not ACTIVE — cannot submit an answer"
                )

            prior_turns = (
                await session.execute(
                    select(DiscoveryTurn)
                    .where(DiscoveryTurn.discovery_session_id == discovery_session_id)
                    .order_by(DiscoveryTurn.sequence.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            next_sequence = (prior_turns.sequence + 1) if prior_turns else 1
            last_question = prior_turns.question if prior_turns else None

            turn = DiscoveryTurn(
                tenant_id=tenant_id,
                discovery_session_id=discovery_session_id,
                sequence=next_sequence,
                kind=DiscoveryTurnKind.QUESTION_ANSWER,
                question=last_question,
                answer=answer,
            )
            session.add(turn)
            discovery_session.questions_asked += 1
            await session.commit()
            await session.refresh(discovery_session)
            await session.refresh(turn)

        return await self._process_turn(
            discovery_session_id, turn, discovery_session.blueprint_id, is_initial=False, actor_id=actor_id
        )

    async def _process_turn(
        self,
        discovery_session_id: uuid.UUID,
        turn: DiscoveryTurn,
        blueprint_id: uuid.UUID,
        *,
        is_initial: bool,
        actor_id: uuid.UUID | None,
    ) -> DiscoveryTurnResult:
        tenant_id = turn.tenant_id
        gap_keys = await self._gap_keys(tenant_id, blueprint_id)

        outcome = await self._extraction_service.extract(
            tenant_id,
            discovery_input=turn.answer,
            gap_keys=gap_keys,
            is_initial=is_initial,
            actor_id=actor_id,
        )

        proposed_ids: list[uuid.UUID] = []
        next_question: str | None = None

        async with self._session_factory() as session:
            discovery_session = await session.get(DiscoverySession, discovery_session_id)

            if not outcome.available:
                turn_row = await session.get(DiscoveryTurn, turn.id)
                turn_row.extraction_error = outcome.error_detail
                await session.commit()
                return DiscoveryTurnResult(
                    session=discovery_session,
                    turn=turn_row,
                    proposed_claim_ids=[],
                    next_question=None,
                    extraction_available=False,
                    extraction_error=outcome.error_detail,
                )

        for claim in outcome.result.claims:
            proposed = await self._blueprint_service.propose_claim(
                tenant_id,
                blueprint_id,
                section_key=claim.section_key,
                claim_type=claim.claim_type,
                key=claim.key,
                value=claim.value,
                confidence=claim.confidence,
                provenance=claim.provenance,
                discovery_turn_id=turn.id,
                evidence_ref=f"discovery_turn:{turn.id}",
            )
            proposed_ids.append(proposed.id)

        remaining_gaps = await self._gap_keys(tenant_id, blueprint_id)

        async with self._session_factory() as session:
            discovery_session = await session.get(DiscoverySession, discovery_session_id)
            at_cap = discovery_session.questions_asked >= discovery_session.max_questions
            if not remaining_gaps and not at_cap:
                # Every minimum-bar section already has CONFIRMED coverage
                # via earlier turns; nothing left to ask about capability-
                # relevant gaps. (Sections only reach COMPLETE on claim
                # CONFIRMATION, so a still-PROPOSED claim doesn't close a
                # gap prematurely — matches the "human review before
                # completion" requirement.)
                discovery_session.status = DiscoverySessionStatus.COMPLETED
                next_question = None
            elif at_cap:
                discovery_session.status = DiscoverySessionStatus.COMPLETED
                next_question = None
            else:
                next_question = outcome.result.follow_up_question
                if next_question is not None:
                    turn_row = await session.get(DiscoveryTurn, turn.id)
                    # Store the just-generated follow-up on THIS turn's
                    # `question` field so the *next* submit_answer call can
                    # read "what was asked" without a separate column.
                    turn_row.question = next_question
            await session.commit()
            await session.refresh(discovery_session)
            turn_row = await session.get(DiscoveryTurn, turn.id)

        return DiscoveryTurnResult(
            session=discovery_session,
            turn=turn_row,
            proposed_claim_ids=proposed_ids,
            next_question=next_question,
            extraction_available=True,
            extraction_error=None,
        )

    async def get_session(self, tenant_id: uuid.UUID, discovery_session_id: uuid.UUID) -> DiscoverySession:
        async with self._session_factory() as session:
            discovery_session = await session.get(DiscoverySession, discovery_session_id)
            if discovery_session is None or discovery_session.tenant_id != tenant_id:
                raise DiscoverySessionNotFoundError(f"DiscoverySession {discovery_session_id} not found")
            return discovery_session

    async def get_turns(self, tenant_id: uuid.UUID, discovery_session_id: uuid.UUID) -> list[DiscoveryTurn]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(DiscoveryTurn)
                    .where(
                        DiscoveryTurn.tenant_id == tenant_id,
                        DiscoveryTurn.discovery_session_id == discovery_session_id,
                    )
                    .order_by(DiscoveryTurn.sequence)
                )
            ).scalars().all()
            return list(rows)
