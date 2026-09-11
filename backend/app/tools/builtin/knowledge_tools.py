"""Phase 12: tools wrapping KnowledgeService — /settings/knowledge and the
knowledge API only ever go through these, same pattern as every other
domain. AUTO at the policy layer, permission-gated instead
(READ_KNOWLEDGE/MANAGE_KNOWLEDGE) — same reasoning as automation.* and
notifications.* above."""

from pydantic import BaseModel

from app.models.rbac import Permission
from app.services.knowledge_qa_service import KnowledgeQAService
from app.services.knowledge_retrieval_service import EmbeddingProviderNotConfiguredError, KnowledgeRetrievalService
from app.services.knowledge_service import KnowledgeService
from app.tools.base import ExecutionContext, Tool


class KnowledgeFileRow(BaseModel):
    path: str
    content: str
    updated_by: str | None
    updated_at: str


def _to_row(f) -> KnowledgeFileRow:
    return KnowledgeFileRow(
        path=f.path,
        content=f.content,
        updated_by=str(f.updated_by) if f.updated_by else None,
        updated_at=f.updated_at.isoformat(),
    )


class ListFilesInput(BaseModel):
    prefix: str | None = None


class ListFilesOutput(BaseModel):
    files: list[KnowledgeFileRow]


class ListKnowledgeFiles(Tool):
    name = "knowledge.list_files"
    description = "List the tenant's Company-OS knowledge files, optionally filtered by category prefix."
    input_schema = ListFilesInput
    output_schema = ListFilesOutput
    required_permission = Permission.READ_KNOWLEDGE

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._service = knowledge_service

    async def execute(self, input: ListFilesInput, context: ExecutionContext) -> ListFilesOutput:
        files = await self._service.list_files(context.tenant_id, prefix=input.prefix)
        return ListFilesOutput(files=[_to_row(f) for f in files])


class GetFileInput(BaseModel):
    path: str


class GetKnowledgeFile(Tool):
    name = "knowledge.get_file"
    description = "Get one Company-OS knowledge file's content."
    input_schema = GetFileInput
    output_schema = KnowledgeFileRow
    required_permission = Permission.READ_KNOWLEDGE

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._service = knowledge_service

    async def execute(self, input: GetFileInput, context: ExecutionContext) -> KnowledgeFileRow:
        file = await self._service.get_file(context.tenant_id, input.path)
        if file is None:
            raise ValueError(f"Knowledge file not found: {input.path}")
        return _to_row(file)


class SetFileInput(BaseModel):
    path: str
    content: str


class SetKnowledgeFile(Tool):
    name = "knowledge.set_file"
    description = "Create or update one Company-OS knowledge file."
    input_schema = SetFileInput
    output_schema = KnowledgeFileRow
    required_permission = Permission.MANAGE_KNOWLEDGE

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._service = knowledge_service

    async def execute(self, input: SetFileInput, context: ExecutionContext) -> KnowledgeFileRow:
        file = await self._service.set_file(
            context.tenant_id, input.path, input.content, actor_id=context.actor_id
        )
        return _to_row(file)


class DeleteFileInput(BaseModel):
    path: str


class DeleteFileOutput(BaseModel):
    deleted: bool


class DeleteKnowledgeFile(Tool):
    name = "knowledge.delete_file"
    description = "Delete one Company-OS knowledge file."
    input_schema = DeleteFileInput
    output_schema = DeleteFileOutput
    required_permission = Permission.MANAGE_KNOWLEDGE

    def __init__(self, knowledge_service: KnowledgeService) -> None:
        self._service = knowledge_service

    async def execute(self, input: DeleteFileInput, context: ExecutionContext) -> DeleteFileOutput:
        deleted = await self._service.delete_file(context.tenant_id, input.path, actor_id=context.actor_id)
        return DeleteFileOutput(deleted=deleted)


class IndexFileInput(BaseModel):
    path: str


class IndexFileOutput(BaseModel):
    file_path: str
    status: str
    chunk_count: int


class IndexKnowledgeFile(Tool):
    name = "knowledge.index_file"
    description = "(Re)index one knowledge file for semantic search — chunks and embeds it if its content changed since last index."
    input_schema = IndexFileInput
    output_schema = IndexFileOutput
    required_permission = Permission.MANAGE_KNOWLEDGE

    def __init__(self, retrieval_service: KnowledgeRetrievalService) -> None:
        self._service = retrieval_service

    async def execute(self, input: IndexFileInput, context: ExecutionContext) -> IndexFileOutput:
        result = await self._service.index_file(context.tenant_id, input.path)
        return IndexFileOutput(file_path=result.file_path, status=result.status, chunk_count=result.chunk_count)


class SearchKnowledgeInput(BaseModel):
    query: str
    top_k: int | None = None
    score_threshold: float | None = None
    path_prefix: str | None = None


class SearchResultRow(BaseModel):
    file_path: str
    chunk_index: int
    content: str
    score: float


class SearchKnowledgeOutput(BaseModel):
    results: list[SearchResultRow]
    available: bool
    error_detail: str | None = None


class SearchKnowledge(Tool):
    name = "knowledge.search"
    description = "Real, tenant-scoped semantic search over the Company-OS knowledge base. Returns cited excerpts, never fabricated content."
    input_schema = SearchKnowledgeInput
    output_schema = SearchKnowledgeOutput
    required_permission = Permission.READ_KNOWLEDGE

    def __init__(self, retrieval_service: KnowledgeRetrievalService) -> None:
        self._service = retrieval_service

    async def execute(self, input: SearchKnowledgeInput, context: ExecutionContext) -> SearchKnowledgeOutput:
        try:
            results = await self._service.search(
                context.tenant_id, input.query, top_k=input.top_k,
                score_threshold=input.score_threshold, path_prefix=input.path_prefix,
            )
        except EmbeddingProviderNotConfiguredError as exc:
            return SearchKnowledgeOutput(results=[], available=False, error_detail=str(exc))
        return SearchKnowledgeOutput(
            results=[
                SearchResultRow(file_path=r.file_path, chunk_index=r.chunk_index, content=r.content, score=r.score)
                for r in results
            ],
            available=True,
        )


class AskKnowledgeInput(BaseModel):
    question: str


class CitationRow(BaseModel):
    file_path: str
    chunk_index: int
    score: float


class AskKnowledgeOutput(BaseModel):
    available: bool
    answer: str | None = None
    answered_from_excerpts: bool | None = None
    sources: list[str] | None = None
    citations: list[CitationRow] | None = None
    error_detail: str | None = None


class AskKnowledge(Tool):
    name = "knowledge.ask"
    description = "Ask a real, AI-answered question over the tenant's own knowledge base, bounded to retrieved excerpts with citations — never fabricates an answer when no relevant content exists or no AI provider is configured."
    input_schema = AskKnowledgeInput
    output_schema = AskKnowledgeOutput
    required_permission = Permission.READ_KNOWLEDGE

    def __init__(self, qa_service: KnowledgeQAService) -> None:
        self._service = qa_service

    async def execute(self, input: AskKnowledgeInput, context: ExecutionContext) -> AskKnowledgeOutput:
        result = await self._service.ask(
            context.tenant_id, input.question, actor_type=context.actor_type, actor_id=context.actor_id,
        )
        if not result.available:
            return AskKnowledgeOutput(available=False, error_detail=result.error_detail)
        return AskKnowledgeOutput(
            available=True,
            answer=result.answer.answer,
            answered_from_excerpts=result.answer.answered_from_excerpts,
            sources=result.answer.sources,
            citations=[
                CitationRow(file_path=c.file_path, chunk_index=c.chunk_index, score=c.score)
                for c in (result.citations or [])
            ],
        )
