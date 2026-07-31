from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.domain.background_jobs import (
    BackgroundJob,
    BackgroundJobStatus,
    JobType,
    MonitoringSchedule,
    NewBackgroundJob,
    Notification,
    NotificationSeverity,
    NotificationType,
    ServiceInstance,
    ServiceType,
)
from app.domain.discovery import (
    CandidateStatus,
    Competitor,
    CompetitorCandidate,
    DiscoveryJob,
    DiscoveryStage,
    RelationshipType,
)
from app.domain.documents import (
    CompanyDocument,
    CompanyKnowledgeChunk,
    DocumentIngestionJob,
    DocumentJobStatus,
    DocumentStage,
)
from app.domain.intelligence import CompanyIntelligenceProfile, Snapshot
from app.domain.intelligence_evidence import (
    EvidenceSourceKind,
    EvidenceSourceType,
    IntelligenceCategory,
    IntelligenceEvidence,
)
from app.domain.monitoring import CompetitorSource, SourceStatus
from app.domain.onboarding import (
    CompanyProfile,
    IdempotencyRecord,
    InitializationJob,
    JobStage,
    OnboardingStatus,
    Source,
)
from app.domain.scanning import CompetitorScanJob, CompetitorSnapshot, ScanStage, ScanStatus
from app.domain.signals import CompetitorSignal, SignalStatus, SignalType
from app.domain.strategy import (
    StrategicReport,
    StrategicReportFeedback,
    StrategyOperationalDiagnostics,
    StrategyRun,
    StrategyRunStatus,
)


class RepositoryConflictError(Exception):
    """A unique persistence invariant was violated."""


class RepositoryUnavailableError(Exception):
    """The persistence provider could not complete an operation."""


class BackgroundJobRepository(Protocol):
    async def enqueue(self, *, job: NewBackgroundJob) -> BackgroundJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> BackgroundJob | None: ...
    async def get_active_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None: ...
    async def get_by_deduplication_key(
        self, organization_id: str, deduplication_key: str
    ) -> BackgroundJob | None: ...
    async def claim_next(
        self,
        *,
        worker_id: str,
        supported_job_types: set[JobType],
        now: datetime,
        lease_duration: timedelta,
        excluded_organization_id: str | None = None,
    ) -> BackgroundJob | None: ...
    async def renew_lease(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> bool: ...
    async def mark_running(
        self, *, job_id: str, worker_id: str, now: datetime
    ) -> BackgroundJob | None: ...
    async def update_progress(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        progress_percent: int,
        stage: str,
        message: str,
    ) -> BackgroundJob | None: ...
    async def mark_succeeded(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        status: BackgroundJobStatus,
        stage: str,
        message: str,
        result_summary: Mapping[str, Any],
    ) -> BackgroundJob | None: ...
    async def schedule_retry(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        available_at: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None: ...
    async def mark_failed(
        self,
        *,
        job_id: str,
        worker_id: str,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None: ...
    async def mark_dead_lettered(
        self,
        *,
        job_id: str,
        worker_id: str | None,
        now: datetime,
        error_code: str,
        error_message: str,
        error_class: str,
    ) -> BackgroundJob | None: ...
    async def request_cancellation(
        self, organization_id: str, job_id: str, now: datetime
    ) -> BackgroundJob | None: ...
    async def mark_cancelled(
        self, *, job_id: str, worker_id: str | None, now: datetime
    ) -> BackgroundJob | None: ...
    async def recover_expired_leases(self, *, now: datetime, limit: int) -> list[BackgroundJob]: ...
    async def list(
        self,
        organization_id: str,
        *,
        status: BackgroundJobStatus | None,
        cursor: str | None,
        limit: int,
    ) -> list[BackgroundJob]: ...
    async def count_active_for_organization(self, organization_id: str) -> int: ...
    async def count_queued(
        self, *, organization_id: str | None = None, scan_only: bool = False
    ) -> int: ...
    async def operational_counts(self, now: datetime) -> tuple[int | None, int, int]: ...
    async def cleanup_terminal(
        self,
        organization_id: str,
        *,
        succeeded_before: datetime,
        failed_before: datetime,
        limit: int,
    ) -> int: ...


class MonitoringScheduleRepository(Protocol):
    async def create(self, schedule: MonitoringSchedule) -> MonitoringSchedule: ...
    async def get_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> MonitoringSchedule | None: ...
    async def update(
        self, organization_id: str, schedule_id: str, changes: Mapping[str, Any]
    ) -> MonitoringSchedule | None: ...
    async def reserve_due(
        self,
        *,
        scheduler_id: str,
        now: datetime,
        lease_duration: timedelta,
    ) -> MonitoringSchedule | None: ...
    async def release_reservation(
        self,
        *,
        schedule_id: str,
        scheduler_id: str,
        now: datetime,
        next_run_at: datetime | None,
        job_id: str | None,
        degraded_reason: str | None = None,
    ) -> MonitoringSchedule | None: ...


class NotificationRepository(Protocol):
    async def create(self, notification: Notification) -> Notification: ...
    async def list(
        self,
        organization_id: str,
        *,
        unread_only: bool,
        severity: NotificationSeverity | None,
        notification_type: NotificationType | None,
        cursor: str | None,
        limit: int,
    ) -> list[Notification]: ...
    async def unread_count(self, organization_id: str) -> int: ...
    async def mark_read(
        self, organization_id: str, notification_id: str, now: datetime
    ) -> Notification | None: ...
    async def mark_all_read(self, organization_id: str, now: datetime) -> int: ...
    async def cleanup_expired(
        self, organization_id: str, *, before: datetime, limit: int
    ) -> int: ...


class ServiceInstanceRepository(Protocol):
    async def heartbeat(self, instance: ServiceInstance) -> ServiceInstance: ...
    async def has_fresh(self, service_type: ServiceType, threshold: datetime) -> bool: ...
    async def cleanup_expired(self, *, before: datetime, limit: int) -> int: ...


class CompanyProfileRepository(Protocol):
    async def create(self, profile: CompanyProfile) -> CompanyProfile: ...
    async def list_organization_ids(self, *, limit: int) -> list[str]: ...
    async def get_for_organization(self, organization_id: str) -> CompanyProfile | None: ...
    async def get_by_id(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyProfile | None: ...
    async def update(
        self, organization_id: str, company_profile_id: str, changes: Mapping[str, Any]
    ) -> CompanyProfile | None: ...
    async def update_onboarding_status(
        self,
        organization_id: str,
        company_profile_id: str,
        status: OnboardingStatus,
        *,
        workspace_metadata: Mapping[str, str] | None = None,
    ) -> CompanyProfile | None: ...
    async def delete(self, organization_id: str, company_profile_id: str) -> None: ...


class SourceRepository(Protocol):
    async def create(self, source: Source) -> Source: ...
    async def get_primary_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> Source | None: ...
    async def list_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> list[Source]: ...
    async def mark_ready(self, organization_id: str, source_id: str) -> Source | None: ...
    async def update_url(self, organization_id: str, source_id: str, url: str) -> Source | None: ...
    async def mark_failed(self, organization_id: str, source_id: str) -> Source | None: ...
    async def delete(self, organization_id: str, source_id: str) -> None: ...


class InitializationJobRepository(Protocol):
    async def create(self, job: InitializationJob) -> InitializationJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> InitializationJob | None: ...
    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None: ...
    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> InitializationJob | None: ...
    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: JobStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> InitializationJob | None: ...
    async def mark_running(self, organization_id: str, job_id: str) -> InitializationJob | None: ...
    async def mark_succeeded(
        self, organization_id: str, job_id: str
    ) -> InitializationJob | None: ...
    async def mark_failed(
        self,
        organization_id: str,
        job_id: str,
        error_code: str,
        error_message: str,
    ) -> InitializationJob | None: ...
    async def reset_for_retry(
        self, organization_id: str, job_id: str, message: str
    ) -> InitializationJob | None: ...
    async def delete(self, organization_id: str, job_id: str) -> None: ...


class IdempotencyRepository(Protocol):
    async def get(self, organization_id: str, idempotency_key: str) -> IdempotencyRecord | None: ...
    async def create(self, record: IdempotencyRecord) -> IdempotencyRecord: ...
    async def save_response_reference(
        self,
        organization_id: str,
        idempotency_key: str,
        company_profile_id: str,
        initialization_job_id: str,
    ) -> IdempotencyRecord | None: ...
    async def delete(self, organization_id: str, idempotency_key: str) -> None: ...


class SnapshotRepository(Protocol):
    async def create_many(self, snapshots: list[Snapshot]) -> list[Snapshot]: ...
    async def list_for_company(
        self, organization_id: str, company_profile_id: str, *, limit: int = 100
    ) -> list[Snapshot]: ...


class CompanyIntelligenceRepository(Protocol):
    async def save(self, profile: CompanyIntelligenceProfile) -> CompanyIntelligenceProfile: ...
    async def get_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> CompanyIntelligenceProfile | None: ...


class CompanyDocumentRepository(Protocol):
    async def create(self, document: CompanyDocument) -> CompanyDocument: ...
    async def get_by_id(
        self, organization_id: str, document_id: str, *, include_deleted: bool = False
    ) -> CompanyDocument | None: ...
    async def list_for_organization(
        self, organization_id: str, *, include_deleted: bool = False
    ) -> list[CompanyDocument]: ...
    async def update(
        self, organization_id: str, document_id: str, changes: Mapping[str, Any]
    ) -> CompanyDocument | None: ...
    async def list_expired_pending(
        self, organization_id: str, *, before: datetime, limit: int
    ) -> list[CompanyDocument]: ...


class DocumentIngestionJobRepository(Protocol):
    async def create(self, job: DocumentIngestionJob) -> DocumentIngestionJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> DocumentIngestionJob | None: ...
    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: DocumentStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> DocumentIngestionJob | None: ...
    async def finalize(
        self,
        organization_id: str,
        job_id: str,
        status: DocumentJobStatus,
        *,
        warning_codes: list[str] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> DocumentIngestionJob | None: ...
    async def reset_for_retry(
        self, organization_id: str, job_id: str, message: str
    ) -> DocumentIngestionJob | None: ...


class CompanyKnowledgeChunkRepository(Protocol):
    async def create_many(
        self, chunks: list[CompanyKnowledgeChunk]
    ) -> list[CompanyKnowledgeChunk]: ...
    async def get_many(
        self, organization_id: str, chunk_ids: list[str]
    ) -> list[CompanyKnowledgeChunk]: ...
    async def list_for_document_version(
        self, organization_id: str, document_id: str, version: int
    ) -> list[CompanyKnowledgeChunk]: ...
    async def activate_version(
        self, organization_id: str, document_id: str, version: int
    ) -> None: ...
    async def delete_version(
        self, organization_id: str, document_id: str, version: int
    ) -> None: ...
    async def delete_for_document(self, organization_id: str, document_id: str) -> None: ...


class DiscoveryJobRepository(Protocol):
    async def create(self, job: DiscoveryJob) -> DiscoveryJob: ...
    async def get_by_id(self, organization_id: str, job_id: str) -> DiscoveryJob | None: ...
    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> DiscoveryJob | None: ...
    async def get_latest_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None: ...
    async def get_active_for_company(
        self, organization_id: str, company_profile_id: str
    ) -> DiscoveryJob | None: ...
    async def mark_running(self, organization_id: str, job_id: str) -> DiscoveryJob | None: ...
    async def set_queries(
        self, organization_id: str, job_id: str, queries: list[str]
    ) -> DiscoveryJob | None: ...
    async def update_progress(
        self,
        organization_id: str,
        job_id: str,
        stage: DiscoveryStage,
        progress_percent: int,
        message: str,
    ) -> DiscoveryJob | None: ...
    async def mark_awaiting_review(
        self,
        organization_id: str,
        job_id: str,
        *,
        candidate_count: int,
        provider_error_count: int,
        partial: bool,
    ) -> DiscoveryJob | None: ...
    async def mark_failed(
        self, organization_id: str, job_id: str, error_code: str, error_message: str
    ) -> DiscoveryJob | None: ...
    async def update_review_counts(
        self, organization_id: str, job_id: str, accepted: int, rejected: int
    ) -> DiscoveryJob | None: ...


class CompetitorCandidateRepository(Protocol):
    async def create_many(
        self, candidates: list[CompetitorCandidate]
    ) -> list[CompetitorCandidate]: ...
    async def get_by_id(
        self, organization_id: str, candidate_id: str
    ) -> CompetitorCandidate | None: ...
    async def list(
        self,
        organization_id: str,
        *,
        candidate_status: CandidateStatus | None,
        relationship_type: RelationshipType | None,
        minimum_score: float,
        discovery_job_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorCandidate]: ...
    async def mark_reviewed(
        self,
        organization_id: str,
        candidate_id: str,
        candidate_status: CandidateStatus,
        user_id: str,
    ) -> CompetitorCandidate | None: ...
    async def count_reviews(
        self, organization_id: str, discovery_job_id: str
    ) -> tuple[int, int]: ...


class CompetitorRepository(Protocol):
    async def create(self, competitor: Competitor) -> Competitor: ...
    async def get_by_candidate(
        self, organization_id: str, candidate_id: str
    ) -> Competitor | None: ...
    async def get_by_domain(
        self, organization_id: str, canonical_domain: str
    ) -> Competitor | None: ...
    async def get_by_id(self, organization_id: str, competitor_id: str) -> Competitor | None: ...
    async def update(
        self, organization_id: str, competitor_id: str, changes: Mapping[str, Any]
    ) -> Competitor | None: ...
    async def list(self, organization_id: str, *, limit: int) -> list[Competitor]: ...


class CompetitorSourceRepository(Protocol):
    async def create(self, source: CompetitorSource) -> CompetitorSource: ...
    async def get_by_id(
        self, organization_id: str, competitor_id: str, source_id: str
    ) -> CompetitorSource | None: ...
    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
        enabled_only: bool = False,
    ) -> list[CompetitorSource]: ...
    async def update(
        self,
        organization_id: str,
        competitor_id: str,
        source_id: str,
        changes: Mapping[str, Any],
    ) -> CompetitorSource | None: ...
    async def set_status(
        self,
        organization_id: str,
        competitor_id: str,
        source_id: str,
        source_status: SourceStatus,
        *,
        error_code: str | None = None,
    ) -> CompetitorSource | None: ...


class CompetitorScanRepository(Protocol):
    async def create(self, scan: CompetitorScanJob) -> CompetitorScanJob: ...
    async def get_by_id(self, organization_id: str, scan_id: str) -> CompetitorScanJob | None: ...
    async def get_by_idempotency_key(
        self, organization_id: str, idempotency_key: str
    ) -> CompetitorScanJob | None: ...
    async def get_active_for_competitor(
        self, organization_id: str, competitor_id: str
    ) -> CompetitorScanJob | None: ...
    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorScanJob]: ...
    async def update_progress(
        self,
        organization_id: str,
        scan_id: str,
        stage: ScanStage,
        progress_percent: int,
        message: str,
        metrics: Mapping[str, int] | None = None,
    ) -> CompetitorScanJob | None: ...
    async def mark_running(
        self, organization_id: str, scan_id: str
    ) -> CompetitorScanJob | None: ...
    async def finalize(
        self,
        organization_id: str,
        scan_id: str,
        scan_status: ScanStatus,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> CompetitorScanJob | None: ...
    async def reset_for_retry(
        self, organization_id: str, scan_id: str, message: str
    ) -> CompetitorScanJob | None: ...


class CompetitorSnapshotRepository(Protocol):
    async def create(self, snapshot: CompetitorSnapshot) -> CompetitorSnapshot: ...
    async def get_by_id(
        self, organization_id: str, snapshot_id: str
    ) -> CompetitorSnapshot | None: ...
    async def get_latest_for_source(
        self, organization_id: str, source_id: str
    ) -> CompetitorSnapshot | None: ...
    async def get_for_scan_source(
        self, organization_id: str, scan_id: str, source_id: str
    ) -> CompetitorSnapshot | None: ...
    async def list_for_scan(
        self, organization_id: str, scan_id: str, *, limit: int = 100
    ) -> list[CompetitorSnapshot]: ...
    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[CompetitorSnapshot]: ...


class CompetitorSignalRepository(Protocol):
    async def create(self, signal: CompetitorSignal) -> CompetitorSignal: ...
    async def get_by_id(self, organization_id: str, signal_id: str) -> CompetitorSignal | None: ...
    async def list_for_competitor(
        self,
        organization_id: str,
        competitor_id: str,
        *,
        signal_type: SignalType | None,
        signal_status: SignalStatus | None,
        minimum_importance: float,
        source_id: str | None,
        observed_after: datetime | None,
        observed_before: datetime | None,
        scan_id: str | None,
        cursor: str | None,
        limit: int,
    ) -> list[CompetitorSignal]: ...
    async def update_status(
        self, organization_id: str, signal_id: str, signal_status: SignalStatus
    ) -> CompetitorSignal | None: ...


class IntelligenceEvidenceRepository(Protocol):
    async def create_many(
        self, evidence: list[IntelligenceEvidence]
    ) -> list[IntelligenceEvidence]: ...
    async def get_many(
        self, organization_id: str, evidence_ids: list[str], *, active_only: bool = True
    ) -> list[IntelligenceEvidence]: ...
    async def list_for_version(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
        version_key: str,
    ) -> list[IntelligenceEvidence]: ...
    async def list_for_source(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
        *,
        active_only: bool = False,
    ) -> list[IntelligenceEvidence]: ...
    async def activate_version(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
        version_key: str,
    ) -> None: ...
    async def delete_version(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
        version_key: str,
    ) -> None: ...
    async def deactivate_source(
        self,
        organization_id: str,
        source_kind: EvidenceSourceKind,
        source_reference_id: str,
    ) -> list[str]: ...
    async def list_active(
        self,
        organization_id: str,
        *,
        competitor_id: str | None = None,
        competitor_ids: list[str] | None = None,
        categories: list[IntelligenceCategory] | None = None,
        source_types: list[EvidenceSourceType] | None = None,
        observed_after: datetime | None = None,
        observed_before: datetime | None = None,
        cursor: str | None = None,
        limit: int = 100,
    ) -> list[IntelligenceEvidence]: ...
    async def count_status(self, organization_id: str) -> tuple[int, int, datetime | None]: ...


class StrategyRunRepository(Protocol):
    async def create(self, run: StrategyRun) -> StrategyRun: ...
    async def get_by_id(self, organization_id: str, run_id: str) -> StrategyRun | None: ...
    async def get_active_by_request_hash(
        self, organization_id: str, request_hash: str
    ) -> StrategyRun | None: ...
    async def get_active_for_organization(self, organization_id: str) -> StrategyRun | None: ...
    async def update(
        self, organization_id: str, run_id: str, changes: Mapping[str, Any]
    ) -> StrategyRun | None: ...
    async def list(
        self,
        organization_id: str,
        *,
        status: StrategyRunStatus | None,
        cursor: str | None,
        limit: int,
    ) -> list[StrategyRun]: ...
    async def count_active(self, organization_id: str | None = None) -> int: ...
    async def operational_diagnostics(self) -> StrategyOperationalDiagnostics: ...


class StrategicReportRepository(Protocol):
    async def create(self, report: StrategicReport) -> StrategicReport: ...
    async def get_by_id(self, organization_id: str, report_id: str) -> StrategicReport | None: ...
    async def get_by_run(
        self, organization_id: str, strategy_run_id: str
    ) -> StrategicReport | None: ...
    async def list(
        self, organization_id: str, *, cursor: str | None, limit: int
    ) -> list[StrategicReport]: ...


class StrategicReportFeedbackRepository(Protocol):
    async def upsert(self, feedback: StrategicReportFeedback) -> StrategicReportFeedback: ...


class Persistence(Protocol):
    background_jobs: BackgroundJobRepository
    monitoring_schedules: MonitoringScheduleRepository
    notifications: NotificationRepository
    service_instances: ServiceInstanceRepository
    company_profiles: CompanyProfileRepository
    sources: SourceRepository
    initialization_jobs: InitializationJobRepository
    idempotency_records: IdempotencyRepository
    snapshots: SnapshotRepository
    company_intelligence_profiles: CompanyIntelligenceRepository
    company_documents: CompanyDocumentRepository
    document_ingestion_jobs: DocumentIngestionJobRepository
    company_knowledge_chunks: CompanyKnowledgeChunkRepository
    discovery_jobs: DiscoveryJobRepository
    competitor_candidates: CompetitorCandidateRepository
    competitors: CompetitorRepository
    competitor_sources: CompetitorSourceRepository
    competitor_scans: CompetitorScanRepository
    competitor_snapshots: CompetitorSnapshotRepository
    competitor_signals: CompetitorSignalRepository
    intelligence_evidence: IntelligenceEvidenceRepository
    strategy_runs: StrategyRunRepository
    strategy_reports: StrategicReportRepository
    strategy_report_feedback: StrategicReportFeedbackRepository

    async def start(self) -> None: ...
    async def ping(self) -> bool: ...
    async def close(self) -> None: ...


def mongo_update_fields(changes: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    """Build a safe mutable-field update document."""
    return {**changes, "updated_at": now}
