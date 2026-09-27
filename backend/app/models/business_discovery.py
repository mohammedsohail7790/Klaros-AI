"""Phase 2 (KLAROS_BUSINESS_DISCOVERY_SPEC.md): the Business Discovery
Engine's own transient conversation-state tables.

Per KLAROS_ARCHITECTURE_RECONCILIATION.md #4, Discovery's own tables
(`DiscoverySession`, `DiscoveryTurn`) are conceptually independent of the
durable Blueprint tables (`BusinessBlueprint`/`BlueprintSection`/
`BlueprintClaim`, in app/models/business_blueprint.py) — a DiscoverySession
can exist and be exercised without a Blueprint ever being activated. This
implementation ships both table families in the same migration (this
prompt's own "Phase 2" spans what the docs call Phase 2 + Phase 3), but
keeps the two model files/concerns separate to preserve that boundary for
any future split.

Shape resolution (KLAROS_BUSINESS_DISCOVERY_SPEC.md §4 describes
`DiscoverySession.messages` as an inline JSONB array with no child table;
KLAROS_FINAL_DOMAIN_MODEL.md and KLAROS_FINAL_API_ARCHITECTURE.md both
independently describe/assume a `DiscoveryTurn` child table). Per this
task's resolution order (reconciliation -> final domain model -> blueprint
spec -> actual implementation), and since no code existed to break either
way, we follow KLAROS_FINAL_DOMAIN_MODEL.md's `DiscoveryTurn` child-table
shape and its ACTIVE/COMPLETED/PROMOTED lifecycle — it is the more complete
of the two conflicting descriptions and matches the API Architecture doc's
"writes DiscoveryTurn" endpoint description. See PHASE_2_IMPLEMENTATION_LOG.md
for the full record of this decision.
"""

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TenantScopedMixin

# Hard cap on adaptive follow-up questions per session (KLAROS_BUSINESS_
# DISCOVERY_SPEC.md §4: "A hard cap (configurable, default 8 follow-up
# questions per session) prevents runaway interrogation"). Configurable via
# BusinessDiscoveryService's constructor, this is only the documented
# default.
DEFAULT_MAX_QUESTIONS = 8


class DiscoverySessionStatus(StrEnum):
    """KLAROS_FINAL_DOMAIN_MODEL.md: "ACTIVE -> COMPLETED (min-bar
    requirements satisfied or capped question count reached) -> PROMOTED"."""

    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    PROMOTED = "PROMOTED"
    ABANDONED = "ABANDONED"  # explicit user/owner cancel; not in either
    # source doc's enum literally, but both specs describe the "user
    # abandons discovery" failure mode (KLAROS_BUSINESS_DISCOVERY_SPEC.md
    # §4 even names it ABANDONED in its own alternate enum) and every other
    # lifecycle-bearing entity in this codebase (Automation, Contract, ...)
    # has an explicit terminal cancel state distinct from its "done" state.


class DiscoveryTurnKind(StrEnum):
    INITIAL_DESCRIPTION = "INITIAL_DESCRIPTION"
    QUESTION_ANSWER = "QUESTION_ANSWER"


class DiscoverySession(TenantScopedMixin, Base):
    """Transient conversation state for the Business Discovery flow — see
    module docstring. `blueprint_id` is nullable and set (a) at session
    creation, pointing at the tenant's current DRAFT blueprint (created on
    demand if none exists yet), so extraction can write BlueprintClaim rows
    directly as the conversation progresses, per KLAROS_BUSINESS_DISCOVERY_
    SPEC.md §3's pipeline ("Draft BlueprintClaim rows... Gap check...").
    """

    __tablename__ = "discovery_sessions"

    blueprint_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("business_blueprints.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DiscoverySessionStatus.ACTIVE, index=True
    )
    business_idea: Mapped[str] = mapped_column(Text, nullable=False)
    questions_asked: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_questions: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_MAX_QUESTIONS)
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


class DiscoveryTurn(TenantScopedMixin, Base):
    """One question/answer pair (or the session-opening free-text
    description) within a DiscoverySession. `answer` is always persisted
    verbatim, independent of whether AI extraction against it succeeds —
    KLAROS_BUSINESS_DISCOVERY_SPEC.md's "never corrupt the Discovery
    session or lose the user's raw input" requirement."""

    __tablename__ = "discovery_turns"

    discovery_session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("discovery_sessions.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default=DiscoveryTurnKind.QUESTION_ANSWER)
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    # Set when the extraction pass against this turn's answer failed (AI
    # unavailable, malformed response) so a caller/UI can offer retry
    # without losing the raw answer above — never silently swallowed.
    extraction_error: Mapped[str | None] = mapped_column(Text, nullable=True)
