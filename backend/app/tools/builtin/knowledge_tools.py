"""Phase 12: tools wrapping KnowledgeService — /settings/knowledge and the
knowledge API only ever go through these, same pattern as every other
domain. AUTO at the policy layer, permission-gated instead
(READ_KNOWLEDGE/MANAGE_KNOWLEDGE) — same reasoning as automation.* and
notifications.* above."""

from pydantic import BaseModel

from app.models.rbac import Permission
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
