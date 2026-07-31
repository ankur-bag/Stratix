from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class JobType(StrEnum):
    COMPANY_INITIALIZATION = "company_initialization"
    COMPETITOR_BASELINE_SCAN = "competitor_baseline_scan"
    COMPETITOR_RESCAN = "competitor_rescan"
    DOCUMENT_INGESTION = "document_ingestion"
    COMPANY_INTELLIGENCE_REBUILD = "company_intelligence_rebuild"
    PINECONE_CLEANUP = "pinecone_cleanup"
    DOCUMENT_STORAGE_CLEANUP = "document_storage_cleanup"
    RETENTION_CLEANUP = "retention_cleanup"
    COMPETITOR_SNAPSHOT_INDEXING = "competitor_snapshot_indexing"
    COMPETITOR_SIGNAL_INDEXING = "competitor_signal_indexing"
    COMPETITOR_INTELLIGENCE_REINDEX = "competitor_intelligence_reindex"
    COMPETITOR_EVIDENCE_CLEANUP = "competitor_evidence_cleanup"
    STRATEGIC_ANALYSIS = "strategic_analysis"


class BackgroundJobStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    RUNNING = "running"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    PARTIALLY_SUCCEEDED = "partially_succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


ACTIVE_JOB_STATUSES = {
    BackgroundJobStatus.QUEUED,
    BackgroundJobStatus.CLAIMED,
    BackgroundJobStatus.RUNNING,
    BackgroundJobStatus.RETRY_SCHEDULED,
    BackgroundJobStatus.CANCEL_REQUESTED,
}


class BackgroundJob(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str | None = None
    job_type: JobType
    domain_resource_type: str
    domain_resource_id: str
    workflow_version: str = "1"
    status: BackgroundJobStatus = BackgroundJobStatus.QUEUED
    priority: int = Field(default=0, ge=-100, le=100)
    deduplication_key: str = Field(min_length=1, max_length=300)
    payload: dict[str, str | int | bool | None] = Field(default_factory=dict)
    payload_version: int = Field(default=1, ge=1)
    attempt_count: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=4, ge=1)
    available_at: datetime
    scheduled_for: datetime | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None
    completed_at: datetime | None = None
    cancelled_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    worker_id: str | None = None
    last_worker_id: str | None = None
    progress_percent: int = Field(default=0, ge=0, le=100)
    stage: str = "queued"
    message: str = Field(default="Background job is queued.", max_length=500)
    result_summary: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    error_class: str | None = None
    retryable: bool | None = None
    created_by_user_id: str | None = None
    created_at: datetime
    updated_at: datetime


class NewBackgroundJob(BaseModel):
    organization_id: str
    company_profile_id: str | None = None
    job_type: JobType
    domain_resource_type: str
    domain_resource_id: str
    workflow_version: str = "1"
    priority: int = Field(default=0, ge=-100, le=100)
    deduplication_key: str = Field(min_length=1, max_length=300)
    payload: dict[str, str | int | bool | None] = Field(default_factory=dict)
    payload_version: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=4, ge=1)
    available_at: datetime
    scheduled_for: datetime | None = None
    created_by_user_id: str | None = None


class BackgroundJobPage(BaseModel):
    items: list[BackgroundJob]
    next_cursor: str | None = None


class BackgroundJobResponse(BaseModel):
    job: BackgroundJob


class RetryJobResponse(BaseModel):
    job: BackgroundJob
    retried_from_job_id: str


class JobExecutionResult(BaseModel):
    status: BackgroundJobStatus = BackgroundJobStatus.SUCCEEDED
    stage: str = "completed"
    message: str = "Background job completed."
    result_summary: dict[str, Any] = Field(default_factory=dict)


class MonitoringCadence(StrEnum):
    MANUAL = "manual"
    DAILY = "daily"
    EVERY_3_DAYS = "every_3_days"
    WEEKLY = "weekly"


class MonitoringSchedule(BaseModel):
    id: str
    organization_id: str
    company_profile_id: str
    competitor_id: str
    schedule_type: str = "competitor_scan"
    cadence: MonitoringCadence = MonitoringCadence.WEEKLY
    timezone: str = "UTC"
    is_enabled: bool = True
    next_run_at: datetime | None = None
    last_scheduled_at: datetime | None = None
    last_job_id: str | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    degraded_reason: str | None = None
    scheduler_lease_owner: str | None = None
    scheduler_lease_expires_at: datetime | None = None
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class MonitoringScheduleUpdate(BaseModel):
    is_enabled: bool
    cadence: MonitoringCadence
    timezone: str = Field(default="UTC", min_length=1, max_length=100)


class NotificationType(StrEnum):
    NEW_COMPETITOR_SIGNALS = "new_competitor_signals"
    SCAN_FAILED = "scan_failed"
    INITIALIZATION_FAILED = "initialization_failed"
    DOCUMENT_PROCESSING_FAILED = "document_processing_failed"
    MONITORING_PAUSED = "monitoring_paused"
    JOB_DEAD_LETTERED = "job_dead_lettered"
    COMPETITOR_EVIDENCE_INDEXING_FAILED = "competitor_evidence_indexing_failed"
    COMPETITOR_INTELLIGENCE_REINDEX_COMPLETED = "competitor_intelligence_reindex_completed"
    COMPETITOR_INTELLIGENCE_REINDEX_FAILED = "competitor_intelligence_reindex_failed"
    STRATEGIC_REPORT_READY = "strategic_report_ready"
    STRATEGIC_REPORT_FAILED = "strategic_report_failed"
    STRATEGIC_REPORT_PARTIAL = "strategic_report_partial"


class NotificationSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Notification(BaseModel):
    id: str
    organization_id: str
    user_id: str | None = None
    company_profile_id: str | None = None
    type: NotificationType
    severity: NotificationSeverity
    title: str = Field(max_length=200)
    message: str = Field(max_length=500)
    resource_type: str
    resource_id: str
    action_url: str | None = None
    is_read: bool = False
    read_at: datetime | None = None
    deduplication_key: str
    created_at: datetime
    expires_at: datetime | None = None


class NotificationPage(BaseModel):
    items: list[Notification]
    next_cursor: str | None = None


class UnreadCountResponse(BaseModel):
    unread_count: int = Field(ge=0)


class ServiceType(StrEnum):
    WORKER = "worker"
    SCHEDULER = "scheduler"


class ServiceInstance(BaseModel):
    instance_id: str
    service_type: ServiceType
    started_at: datetime
    heartbeat_at: datetime
    version: str
    supported_job_types: list[JobType] = Field(default_factory=list)


class QueueHealth(BaseModel):
    worker_available: bool
    scheduler_available: bool
    queue_degraded: bool
    oldest_queued_job_age_seconds: int | None = None
    expired_lease_count: int = 0
    dead_letter_count: int = 0
