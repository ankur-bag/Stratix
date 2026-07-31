import type {
  EvidenceSourceType,
  IntelligenceCategory,
  IntelligenceSearchInput,
} from "@/lib/api/types";

export type IntelligenceFilters = {
  competitorId: string;
  category: IntelligenceCategory | "";
  sourceType: EvidenceSourceType | "";
  dateFrom: string;
  dateTo: string;
  includeCompanyContext: boolean;
};

export function buildIntelligenceSearchInput(
  query: string,
  filters: IntelligenceFilters,
): IntelligenceSearchInput {
  return {
    query: query.trim().replace(/\s+/g, " "),
    competitor_ids: filters.competitorId ? [filters.competitorId] : [],
    categories: filters.category ? [filters.category] : [],
    source_types: filters.sourceType ? [filters.sourceType] : [],
    ...(filters.dateFrom
      ? { observed_after: `${filters.dateFrom}T00:00:00.000Z` }
      : {}),
    ...(filters.dateTo
      ? { observed_before: `${filters.dateTo}T23:59:59.999Z` }
      : {}),
    include_company_context: filters.includeCompanyContext,
    limit: 12,
  };
}

export function intelligenceLabel(value: string): string {
  return value
    .split("_")
    .map((part) => `${part.charAt(0).toUpperCase()}${part.slice(1)}`)
    .join(" ");
}

export function evidenceDate(value: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
