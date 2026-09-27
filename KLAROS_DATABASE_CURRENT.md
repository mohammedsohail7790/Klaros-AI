# Klaros Database — Current State

Source: exhaustive `grep -n "^class "` over `backend/app/models/*.py` (23 files, ~208 model classes — see full raw list captured in this audit session), `backend/alembic/versions/` (40 migration files, sequential 0001–0039+), and direct reads of `organization.py`, `rbac.py`, and the knowledge-layer comments in `knowledge_retrieval_service.py`. Individual column/index/constraint detail was **not read for every model** (208 classes is out of scope for full column-level transcription in this pass) — this file lists table families, purpose, tenant scoping, and consumers at the class level, which is accurate and complete at that granularity.

## Engine & isolation model

- PostgreSQL + `pgvector` extension (production). SQLite + `aiosqlite` supported as a genuine second runtime, primarily for tests.
- ORM: SQLAlchemy 2.0 async, `asyncpg` driver.
- Migrations: Alembic, 40 sequential versioned files in `backend/alembic/versions/`.
- Tenant column: `tenant_id`, added by `TenantScopedMixin`, present on essentially every table below except `Organization` itself (the tenant root) and RBAC enums (which are code-level, not DB rows).
- **No Row-Level Security.** No `CREATE POLICY`/`ROW LEVEL SECURITY` found anywhere in migrations or app code. Isolation is enforced entirely in the application layer via `CurrentUser.tenant_id` (from the JWT) filtering every query — see main audit §10 for the associated structural risk.
- Vector storage: `KnowledgeChunk.embedding` — `pgvector` column type (`backend/app/db/vector_type.py`), HNSW-indexed per migration 0032 (per `knowledge_retrieval_service.py:14-18`), with a Python-side cosine-similarity fallback used automatically on SQLite (no pgvector there).

## Table families (by model file)

### Tenancy / Auth (`organization.py`, `user.py`, `rbac.py`)
- `Organization` — tenant root. Columns observed: `name`, `slug` (unique), `plan`, `autonomy_level` (LEVEL_0..LEVEL_4 — AI autonomy governance), `billing_status`, `stripe_customer_id`, `stripe_subscription_id`, `trial_ends_at`, `current_period_end`, `manual_starting_cash` (explicitly "MANUAL/INTERNAL TEST DATA only — no bank integration exists," `organization.py:44-45`), `morning_brief_enabled`/`morning_brief_local_time`/`morning_brief_timezone`, `timezone`, plus an AI kill-switch flag (referenced but not fully read, `organization.py:~60+`).
- `User` — tenant-scoped user account, role field references `rbac.Role`.
- `TeamInvite` — pending invites with `InviteStatus`.
- `Role`/`Permission` (in `rbac.py`) — **code-level StrEnum, not DB tables.** No `roles`/`permissions`/`role_permissions` tables exist; authorization is a static Python mapping, not data-driven. This means adding a custom role or changing a permission set requires a code change, not an admin UI action — a real product constraint to be aware of.

### CRM (`crm.py`)
- `Lead` (with `LeadSource`, `LeadStatus`, `QualificationStatus`, `Urgency` enums), `Customer` (`CustomerStatus`), `CustomerNote`, `Appointment` (`AppointmentStatus`, external-provider fields for Google Calendar sync).

### Operations (`operations.py`)
- `Job` (`JobStatus`, `JobPriority`), `Worker` (`WorkerStatus`), `JobTask` (`TaskStatus`), `JobAttachment` (`AttachmentKind`, `TranscriptionStatus` — implies voice-note transcription), `JobMaterial` (`MaterialStatus`), `PurchaseOrder`/`PurchaseOrderItem` (`PurchaseOrderStatus`), `ScopeChange` (`ScopeChangeStatus`), `OperationsException` (`ExceptionType`, `ExceptionSeverity`, `ExceptionStatus`), `JobQA` (`QAStatus`), `CompletionPacket` (`PacketStatus`), `CustomerSignoff`.

### Finance (`finance.py`)
- `Invoice`/`InvoiceLineItem` (`InvoiceStatus`), `Payment`/`PaymentAllocation` (`PaymentStatus`), `Refund` (`RefundStatus`), `CreditNote`/`CreditNoteLineItem` (`CreditNoteStatus`), `WriteOffRequest` (`WriteOffStatus`), `JobCost` (`JobCostCategory`), `Vendor` (`VendorStatus`), `VendorBill` (`VendorBillStatus`), `Payout` (`PayoutStatus`), `CashForecast`/`CashForecastItem` (`ForecastItemType`, `ForecastConfidence`), `CollectionAction` (`CollectionActionType`, `CollectionActionStatus`).

### Quote / Contract (`quote.py`, `contract.py`)
- `Quote`/`QuoteLineItem` (`QuoteStatus`, `DepositType`). `Contract` (`ContractStatus`) — internal attestation only, no third-party e-signature provider integrated (confirmed by `frontend/lib/api.ts:1581-1584` comment).

### Marketing (`marketing.py` — largest single model file, 18+ classes)
- Campaigns: `Campaign` (`CampaignChannel`, `CampaignObjective`, `CampaignStatus`), `MarketingSpend`/`MarketingSpendAllocation`, `MarketingLeadSource`, `LeadAttribution` (`AttributionModel`), `CampaignLead`, `CampaignConversion` (`ConversionStage`).
- Content: `MarketingContent` (`ContentStatus`, `ContentChannel`), `ContentAsset`, `ContentVariant`, `ContentPublication`, `ContentPerformance`.
- SEO/Local: `SEOPage` (`SEOPageStatus`), `SEOKeyword`, `SEOOpportunity`, `LocalListing`, `LocalReview`, `LocalReputationEvent`.
- Outbound: `OutboundList`, `OutboundContact` (`ContactSource`, `EnrichmentStatus`), `OutboundSequence` (`OutboundSequenceStatus`), `OutboundStep`, `OutboundEnrollment` (`EnrollmentStatus`), `OutboundActivity` (`ActivityStatus`).
- Nurture/Reactivation: `NurtureSequence` (`NurtureTriggerType`), `NurtureEnrollment`, `NurtureActivity`, `ReactivationCampaign`, `ReactivationCandidate` (`ReactivationCandidateStatus`).

### Retention (`retention.py` — 18+ classes)
- `CustomerLifecycleProfile` (`LifecycleState`), `RetentionOpportunity` (`OpportunityType`, `OpportunityStatus`), `ServiceReminder` (`ReminderStatus`), `Warranty` (`WarrantyStatus`), `ReviewRequest` (`ReviewStatus`, `ReviewChannel`), `CustomerFeedback` (`FeedbackSentiment`), `ReferralProgram` (`ReferralProgramStatus`), `ReferralCode`, `Referral` (`ReferralStatus`), `ReferralReward` (`RewardStatus`), `CustomerRiskSignal` (`RiskSignalType`, `RiskSeverity`), `AdvocateCandidate` (`AdvocateCandidateStatus`), `RetentionCampaign` (`RetentionCampaignType`, `RetentionCampaignStatus`), `RetentionEnrollment`, `RetentionActivity`.

### Compliance (`compliance.py`)
- `License` (`LicenseType`, `LicenseStatus`).

### Knowledge (`knowledge.py`)
- `KnowledgeFile`, `KnowledgeChunk` (pgvector embedding column, tenant-scoped, content-hash-keyed for idempotent re-indexing).

### Company Memory (`company_memory.py`)
- `CompanyMemory` (`MemoryType`, `MemorySource`, `MemoryStatus`) — structured business-context facts, human- or AI-sourced. Not the same as the knowledge/RAG file layer; not consumed by the voice AI's realtime engine (self-documented gap, main audit §19).

### AI governance (`ai_invocation.py`, `approval.py`, `audit_log.py`, `tool_policy.py`)
- `AIInvocationLog` (tenant-scoped record of AI calls). `ApprovalRequest` (`ApprovalStatus`, `ApprovalExecutionStatus`). `AuditLog`. `TenantToolPolicy` (per-tenant tool policy overrides).

### Automation (`automation.py`)
- `Automation`, `AutomationVersion`, `AutomationExecution` (`ExecutionStatus`), `AutomationExecutionStep` (`StepStatus`) — with `TriggerType` (SCHEDULE/EVENT) and `AutomationStatus`.

### Events (`event.py`)
- `Event` (`EventType`, `EventStatus`), `EventProcessingRecord` (`ProcessingStatus`), `DeadLetterEvent` — real dead-letter handling for failed event processing.

### Integration (`integration.py`)
- `IntegrationConnection` (`ConnectionStatus`) — per-tenant connected provider + encrypted credential. `WebhookEvent` (`WebhookProcessingStatus`) — inbound webhook audit/processing trail.

### Voice (`voice.py`)
- `CallSession` (`CallDirection`, `CallStatus`, `BookingState`, `CallOutcome`), `VoiceReceptionistSettings`.

### Notification (`notification.py`)
- `Notification` (`NotificationType`, `NotificationPriority`, `NotificationChannelName`, `NotificationStatus`), `NotificationPreference`.

### Morning Brief (`morning_brief.py`)
- `MorningBrief` (`MorningBriefMode` — AI vs deterministic), `MorningBriefInsight` (`InsightCategory`, `InsightPriority`), `MorningBriefRecommendation` (`RecommendationStatus`).

### Communication (`communication.py`)
- `CommunicationLog`.

### Actor (`actor.py`)
- `ActorType` (HUMAN vs AI) — used by `ExecutionContext` to distinguish who initiated a tool call, for audit purposes.

## Explicitly absent table families

No product/SKU/inventory/order/fulfillment tables. No supplier-catalog tables (distinct from `Vendor`, which models subcontractor billing, not product sourcing). No provider/hospital/clinic directory tables. No `roles`/`permissions` DB tables (code-level enums only). No business-discovery/requirements/recommendation tables.

## Migration count and cadence

40 files in `backend/alembic/versions/`, numbered sequentially 0001 onward — consistent with the extensive "Phase N" commentary embedded throughout the codebase, suggesting each migration corresponds to a discrete, documented development phase rather than ad hoc schema churn.
