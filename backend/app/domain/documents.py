from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DocumentStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentStage(StrEnum):
    AWAITING_UPLOAD = "awaiting_upload"
    UPLOADED = "uploaded"
    VALIDATING = "validating"
    EXTRACTING_TEXT = "extracting_text"
    CHUNKING = "chunking"
    EMBEDDING = "embedding"
    STORING_VECTORS = "storing_vectors"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


class DocumentJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class CompanyDocument(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str | None = None
    uploaded_by_user_id: str
    original_filename: str
    content_type: str
    storage_key: str
    size_bytes: int = Field(ge=0)
    sha256: str | None = None
    page_count: int | None = Field(default=None, ge=0)
    status: DocumentStatus
    stage: DocumentStage
    progress_percent: int = Field(default=0, ge=0, le=100)
    chunk_count: int = Field(default=0, ge=0)
    active_version: int | None = Field(default=None, ge=1)
    pending_version: int | None = Field(default=None, ge=1)
    ingestion_job_id: str | None = None
    warning_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    deleted_at: datetime | None = None

    @model_validator(mode="after")
    def validate_timestamps(self) -> Self:
        for value in (
            self.created_at,
            self.updated_at,
            self.completed_at,
            self.deleted_at,
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError("Document timestamps must be timezone-aware.")
        return self


class DocumentIngestionJob(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    document_id: str
    requested_by_user_id: str
    version: int = Field(ge=1)
    status: DocumentJobStatus
    stage: DocumentStage
    progress_percent: int = Field(ge=0, le=100)
    message: str = Field(max_length=500)
    page_count: int = Field(default=0, ge=0)
    extracted_character_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)
    embedded_chunk_count: int = Field(default=0, ge=0)
    vector_count: int = Field(default=0, ge=0)
    warning_codes: list[str] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime


class CompanyKnowledgeChunk(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    document_id: str
    document_version: int = Field(ge=1)
    chunk_index: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_hash: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    heading: str | None = None
    embedding_version: str | None = None
    vector_id: str | None = None
    active: bool = False
    created_at: datetime


class UploadRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/pdf", max_length=100)
    size_bytes: int = Field(gt=0)

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        if value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("Filename must not contain a path.")
        return value


class UploadTarget(BaseModel):
    method: str
    url: str
    headers: dict[str, str] = Field(default_factory=dict)


class UploadRequestResponse(BaseModel):
    document: CompanyDocument
    upload: UploadTarget


class UploadCompleteRequest(BaseModel):
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    size_bytes: int = Field(gt=0)


class DocumentListResponse(BaseModel):
    items: list[CompanyDocument]


class DocumentJobResponse(BaseModel):
    job: DocumentIngestionJob


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1)
    document_ids: list[str] | None = Field(default=None, max_length=50)


class KnowledgeSearchResult(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    score: float
    excerpt: str
    page_start: int
    page_end: int
    heading: str | None = None


class KnowledgeSearchResponse(BaseModel):
    items: list[KnowledgeSearchResult]
    vector_search_enabled: bool
    warning_code: str | None = None


class ExtractedPage(BaseModel):
    page_number: int = Field(ge=1)
    text: str


class ExtractedPdf(BaseModel):
    pages: list[ExtractedPage]
    page_count: int = Field(ge=1)
    character_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)


class ChunkDraft(BaseModel):
    index: int = Field(ge=0)
    text: str
    text_hash: str
    page_start: int = Field(ge=1)
    page_end: int = Field(ge=1)
    heading: str | None = None
