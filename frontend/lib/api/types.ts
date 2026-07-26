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

export type CompetitorScanResponse = { scan: CompetitorScan };
export type CompetitorScanPage = { items: CompetitorScan[]; next_cursor: string | null };

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
