export type JobSummary = {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: string;
  progress_percent: number;
  message: string | null;
};

export type OnboardingStatus = {
  has_company_profile: boolean;
  onboarding_status: "submitted" | "initializing" | "ready" | "failed" | null;
  company_profile_id: string | null;
  latest_job: JobSummary | null;
};

export type CompanyOnboardingRequest = {
  company_name: string;
  website_url: string;
  one_line_description: string;
  detailed_description?: string;
  industry: string;
  business_stage: "idea" | "pre_seed" | "seed" | "series_a" | "growth" | "established";
  business_model?: "saas" | "marketplace" | "ecommerce" | "services" | "consumer" | "open_source" | "hybrid" | "other";
  target_customer_segments: string[];
  product_name: string;
  product_categories: string[];
  primary_use_cases: string[];
  differentiators: string[];
  keywords: string[];
  known_competitors: string[];
  strategic_goals: string[];
  document_ids?: string[];
};

export type OnboardingResponse = {
  company_profile: { id: string; onboarding_status: string };
  initialization_job: JobSummary;
};

export type InitializationJob = JobSummary & {
  error_code: string | null;
  error_message: string | null;
};

export type ReinitializeResponse = {
  initialization_job_id: string;
  status: string;
  stage: string;
  progress_percent: number;
};

export type CompanyDocument = {
  id: string;
  company_profile_id: string | null;
  original_filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string | null;
  page_count: number | null;
  status: "pending_upload" | "uploaded" | "processing" | "ready" | "failed" | "deleted";
  stage: "awaiting_upload" | "uploaded" | "validating" | "extracting_text" | "chunking" | "embedding" | "storing_vectors" | "completed" | "failed" | "deleted";
  progress_percent: number;
  chunk_count: number;
  active_version: number | null;
  ingestion_job_id: string | null;
  warning_codes: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type DocumentUploadRequest = {
  filename: string;
  content_type: "application/pdf";
  size_bytes: number;
};

export type DocumentUploadResponse = {
  document: CompanyDocument;
  upload: {
    method: string;
    url: string;
    headers: Record<string, string>;
  };
};

export type DocumentIngestionJob = {
  id: string;
  document_id: string;
  version: number;
  status: "queued" | "running" | "succeeded" | "failed";
  stage: CompanyDocument["stage"];
  progress_percent: number;
  message: string;
  page_count: number;
  chunk_count: number;
  vector_count: number;
  warning_codes: string[];
  error_code: string | null;
  error_message: string | null;
};

export type DiscoveryJob = {
  id: string;
  status: "queued" | "running" | "succeeded" | "partially_succeeded" | "failed";
  stage: string;
  progress_percent: number;
  message: string;
  candidate_count: number;
  provider_error_count: number;
  error_code: string | null;
  error_message: string | null;
};

export type DiscoveryJobResponse = {
  job: Pick<DiscoveryJob, "id" | "status" | "stage" | "progress_percent" | "message">;
};

export type DiscoveryEvidence = {
  query: string;
  title: string;
  result_url: string;
  snippet: string;
  provider: string;
  provider_score: number;
};

export type CompetitorCandidate = {
  id: string;
  discovery_job_id: string;
  name: string;
  canonical_url: string;
  canonical_domain: string;
  description: string;
  relationship_type: "direct" | "indirect" | "adjacent" | "emerging" | "unknown";
  status: "pending" | "approved" | "rejected";
  similarity_score: number;
  confidence: number;
  rank: number;
  matching_categories: string[];
  matching_use_cases: string[];
  matching_keywords: string[];
  reasons: string[];
  evidence: DiscoveryEvidence[];
};

export type CandidatePage = {
  items: CompetitorCandidate[];
  next_cursor: string | null;
};

export type Competitor = {
  id: string;
  name: string;
  canonical_url: string;
  canonical_domain: string;
  description: string;
  relationship_type: CompetitorCandidate["relationship_type"];
  status: "active" | "paused" | "archived";
  monitoring_status: "pending" | "initializing" | "active" | "paused" | "failed" | "archived";
  baseline_status: "not_started" | "discovering_sources" | "collecting" | "ready" | "partial" | "failed";
  last_scan_at: string | null;
  last_successful_scan_at: string | null;
  next_scan_at: string | null;
  latest_scan_job_id: string | null;
  source_count: number;
  signal_count: number;
};

export type CompetitorSourceType = "website" | "pricing" | "product" | "features" | "solutions" | "documentation" | "changelog" | "blog" | "customers" | "integrations" | "about";

export type CompetitorSource = {
  id: string;
  competitor_id: string;
  type: CompetitorSourceType;
  url: string;
  canonical_url: string;
  canonical_domain: string;
  enabled: boolean;
  status: "pending" | "ready" | "disabled" | "blocked" | "failed";
  priority: "high" | "medium" | "low";
  discovered_automatically: boolean;
  page_type: string;
  last_scanned_at: string | null;
  last_successful_scan_at: string | null;
  failure_count: number;
  last_error_code: string | null;
};

export type CompetitorSourcePage = {
  items: CompetitorSource[];
  next_cursor: string | null;
};

export type CompetitorSourceCreate = {
  url: string;
  type?: CompetitorSourceType;
  priority?: "high" | "medium" | "low";
};

export type CompetitorSourceUpdate = {
  enabled?: boolean;
  type?: CompetitorSourceType;
  priority?: "high" | "medium" | "low";
};

export type CompetitorScan = {
  id: string;
  competitor_id: string;
  scan_type: "baseline" | "rescan";
  trigger: "approval" | "manual" | "reinitialize" | "future_schedule";
  status: "queued" | "running" | "succeeded" | "partially_succeeded" | "failed";
  stage: string;
  progress_percent: number;
  message: string;
  source_count: number;
  successful_source_count: number;
  failed_source_count: number;
  snapshot_count: number;
  signal_count: number;
  error_code: string | null;
  error_message: string | null;
  rediscover_sources: boolean;
  created_at: string;
  completed_at: string | null;
};

export type BackgroundJob = {
  id: string;
  job_type: "company_initialization" | "competitor_baseline_scan" | "competitor_rescan" | "document_ingestion" | "company_intelligence_rebuild" | "pinecone_cleanup" | "document_storage_cleanup" | "retention_cleanup" | "competitor_snapshot_indexing" | "competitor_signal_indexing" | "competitor_intelligence_reindex" | "competitor_evidence_cleanup" | "strategic_analysis";
  domain_resource_type: string;
  domain_resource_id: string;
  status: "queued" | "claimed" | "running" | "retry_scheduled" | "succeeded" | "partially_succeeded" | "failed" | "dead_lettered" | "cancel_requested" | "cancelled";
  attempt_count: number;
  max_attempts: number;
  progress_percent: number;
  stage: string;
  message: string;
  error_code: string | null;
  error_message: string | null;
  retryable: boolean | null;
  created_at: string;
};

export type CompetitorScanResponse = {
  scan: CompetitorScan;
  job: BackgroundJob | null;
};
export type CompetitorScanPage = { items: CompetitorScan[]; next_cursor: string | null };

export type MonitoringCadence = "manual" | "daily" | "every_3_days" | "weekly";

export type MonitoringSchedule = {
  id: string;
  competitor_id: string;
  cadence: MonitoringCadence;
  timezone: string;
  is_enabled: boolean;
  next_run_at: string | null;
  last_scheduled_at: string | null;
  last_job_id: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  degraded_reason: string | null;
};

export type NotificationType =
  | "new_competitor_signals"
  | "scan_failed"
  | "initialization_failed"
  | "document_processing_failed"
  | "monitoring_paused"
  | "job_dead_lettered"
  | "competitor_evidence_indexing_failed"
  | "competitor_intelligence_reindex_completed"
  | "competitor_intelligence_reindex_failed"
  | "strategic_report_ready"
  | "strategic_report_failed"
  | "strategic_report_partial";

export type Notification = {
  id: string;
  type: NotificationType;
  severity: "info" | "warning" | "error";
  title: string;
  message: string;
  resource_type: string;
  resource_id: string;
  action_url: string | null;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
};

export type NotificationPage = {
  items: Notification[];
  next_cursor: string | null;
};

export type NumericChange = {
  field: string;
  before: string | null;
  after: string | null;
  currency: string | null;
  change_type: string;
};

export type CompetitorSignal = {
  id: string;
  competitor_id: string;
  source_id: string;
  scan_id: string;
  type: "pricing_change" | "feature_launch" | "feature_removal" | "product_change" | "positioning_change" | "documentation_change" | "changelog_update" | "integration_change" | "customer_proof_change" | "content_change" | "unknown";
  title: string;
  summary: string;
  observed_facts: string[];
  before_excerpt: string | null;
  after_excerpt: string;
  changed_sections: string[];
  numeric_changes: NumericChange[];
  importance_score: number;
  confidence: number;
  status: "new" | "reviewed" | "dismissed";
  source_url: string;
  previous_snapshot_id: string;
  current_snapshot_id: string;
  observed_at: string;
};

export type SignalPage = { items: CompetitorSignal[]; next_cursor: string | null };

export type CandidateReviewResponse = {
  results: Array<{
    candidate: CompetitorCandidate;
    competitor: Competitor | null;
  }>;
};

export type EvidenceSourceType =
  | "company_document"
  | "company_website_snapshot"
  | "competitor_website_snapshot"
  | "competitor_signal"
  | "competitor_pricing_signal"
  | "competitor_feature_signal"
  | "competitor_positioning_signal"
  | "competitor_integration_signal"
  | "competitor_content_signal"
  | "competitor_hiring_signal"
  | "competitor_github_signal";

export type IntelligenceCategory =
  | "pricing"
  | "features"
  | "positioning"
  | "integrations"
  | "content"
  | "documentation"
  | "customer_proof"
  | "product"
  | "general";

export type IntelligenceSearchInput = {
  query: string;
  competitor_ids?: string[];
  categories?: IntelligenceCategory[];
  source_types?: EvidenceSourceType[];
  evidence_kinds?: EvidenceKind[];
  observed_after?: string;
  observed_before?: string;
  include_company_context?: boolean;
  limit?: number;
};

export type EvidenceKind =
  | "snapshot_chunk"
  | "signal_summary"
  | "signal_before_excerpt"
  | "signal_after_excerpt"
  | "document_chunk_reference"
  | "structured_fact";

export type EvidenceExcerpt = {
  evidence_id: string;
  competitor_id: string | null;
  competitor_name?: string | null;
  source_type: EvidenceSourceType;
  source_kind: "snapshot" | "signal" | "document";
  evidence_kind: EvidenceKind;
  signal_category: IntelligenceCategory;
  title: string;
  section_title: string | null;
  content_excerpt: string;
  source_url: string | null;
  observed_at: string;
  snapshot_id?: string | null;
  signal_id?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  before_excerpt?: string | null;
  after_excerpt?: string | null;
  score: number;
};

export type EvidenceBundle = {
  items: EvidenceExcerpt[];
  grouped_by_competitor: Record<string, string[]>;
  coverage: {
    competitor_count: number;
    source_type_count: number;
    category_count: number;
    earliest_observed_at: string | null;
    latest_observed_at: string | null;
  };
  warnings: string[];
};

export type IntelligenceSearchResponse = {
  query: string;
  bundle: EvidenceBundle;
};

export type IntelligenceAnswerResponse = {
  answer: string;
  observations: string[];
  inferences: string[];
  correlations: string[];
  historical_patterns: string[];
  uncertainties: string[];
  evidence_ids: string[];
  competitor_ids: string[];
  date_range: { from: string | null; to: string | null };
  source_coverage: EvidenceSourceType[];
  citations: Array<{ evidence_id: string; label: string }>;
  bundle: EvidenceBundle;
  synthesis_mode: string;
};

export type IntelligenceTrend = {
  window_days: number;
  category: IntelligenceCategory;
  event_count: number;
  competitor_count: number;
  direction: string;
  evidence_ids: string[];
};

export type IntelligenceCorrelation = {
  type: string;
  title: string;
  summary: string;
  competitor_ids: string[];
  evidence_ids: string[];
  confidence: number;
};

export type CompetitorTimelineItem = {
  evidence_id: string;
  source_type: EvidenceSourceType;
  category: IntelligenceCategory;
  title: string;
  excerpt: string;
  before_excerpt: string | null;
  after_excerpt: string | null;
  source_url: string | null;
  observed_at: string;
};

export type CompetitorTimelinePage = {
  items: CompetitorTimelineItem[];
  next_cursor: string | null;
};

export type IntelligenceOverview = {
  recent_changes: CompetitorTimelineItem[];
  correlations: IntelligenceCorrelation[];
  trends: IntelligenceTrend[];
  warnings: string[];
};

export type IntelligenceIndexingStatus = {
  active_evidence_count: number;
  inactive_evidence_count: number;
  pending_job_count: number;
  failed_job_count: number;
  oldest_pending_job_age_seconds: number | null;
  active_version_consistent: boolean;
  indexing_available: boolean;
  last_indexed_at: string | null;
  warnings: string[];
};

export type IntelligenceReindexResponse = {
  job_id: string;
  status: string;
};

export type StrategyMode = "ask" | "compare" | "executive_brief";
export type StrategyRunStatus =
  | "queued"
  | "retrieving_evidence"
  | "analyzing"
  | "validating"
  | "succeeded"
  | "partially_succeeded"
  | "failed"
  | "cancel_requested"
  | "cancelled";

export type StrategyRun = {
  id: string;
  mode: StrategyMode;
  query: string;
  competitor_ids: string[];
  date_from: string;
  date_to: string;
  status: StrategyRunStatus;
  stage: string;
  progress_percent: number;
  message: string;
  report_id: string | null;
  warnings: string[];
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  completed_at: string | null;
};

export type StrategyRunResponse = { run: StrategyRun };
export type StrategyRunPage = { items: StrategyRun[]; next_cursor: string | null };

export type StrategicClaim = {
  statement: string;
  evidence_ids: string[];
  confidence: "low" | "medium" | "high";
  competitor_ids: string[];
  date_from: string | null;
  date_to: string | null;
};

export type StrategicComparison = {
  topic: string;
  comparison: string;
  competitor_ids: string[];
  evidence_ids: string[];
  confidence: "low" | "medium" | "high";
  date_from: string | null;
  date_to: string | null;
};

export type StrategicRecommendation = {
  title: string;
  recommendation: string;
  rationale: string;
  evidence_ids: string[];
  company_context_source_ids: string[];
  competitor_ids: string[];
  impact: "low" | "medium" | "high";
  effort: "low" | "medium" | "high";
  time_horizon: "immediate" | "near_term" | "medium_term";
  confidence: "low" | "medium" | "high";
  assumptions: string[];
  risks: string[];
  success_indicators: string[];
  date_from: string | null;
  date_to: string | null;
};

export type StrategicUncertainty = {
  statement: string;
  evidence_ids: string[];
  impact: "low" | "medium" | "high";
};

export type StrategicEvidenceGap = {
  topic: string;
  description: string;
  why_it_matters: string;
  competitor_ids: string[];
};

export type StrategicEvidenceCoverage = {
  evidence_count: number;
  cited_evidence_count: number;
  competitor_count: number;
  source_type_count: number;
  earliest_observed_at: string | null;
  latest_observed_at: string | null;
  truncated: boolean;
  warnings: string[];
};

export type StrategicReport = {
  id: string;
  strategy_run_id: string;
  mode: StrategyMode;
  title: string;
  executive_summary: string;
  situation_assessment: StrategicClaim[];
  inferences: StrategicClaim[];
  competitor_comparisons: StrategicComparison[];
  threats: StrategicClaim[];
  opportunities: StrategicClaim[];
  recommendations: StrategicRecommendation[];
  uncertainties: StrategicUncertainty[];
  evidence_gaps: StrategicEvidenceGap[];
  evidence_ids: string[];
  competitor_ids: string[];
  evidence_coverage: StrategicEvidenceCoverage;
  source_coverage: string[];
  temporal_coverage: { from: string | null; to: string | null };
  analysis_as_of: string;
  date_from: string;
  date_to: string;
  warnings: string[];
  deterministic_fallback: boolean;
  created_at: string;
};

export type StrategicReportPage = {
  items: StrategicReport[];
  next_cursor: string | null;
};

export type StrategyRunInput = {
  mode: StrategyMode;
  query: string;
  competitor_ids: string[];
  date_from: string;
  date_to: string;
};

export type StrategicFeedbackResponse = {
  report_id: string;
  feedback: "helpful" | "not_helpful";
  comment: string | null;
  updated_at: string;
};
