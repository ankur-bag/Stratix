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
};

export type OnboardingResponse = {
  company_profile: { id: string; onboarding_status: string };
  initialization_job: JobSummary;
};

export type InitializationJob = JobSummary & {
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
};

export type CandidateReviewResponse = {
  results: Array<{
    candidate: CompetitorCandidate;
    competitor: Competitor | null;
  }>;
};
