import assert from "node:assert/strict";
import test from "node:test";
import {
  buildIntelligenceSearchInput,
  evidenceDate,
  intelligenceLabel,
} from "./intelligence.ts";

test("buildIntelligenceSearchInput normalizes and scopes filters", () => {
  assert.deepEqual(
    buildIntelligenceSearchInput("  pricing   changed  ", {
      competitorId: "competitor-1",
      category: "pricing",
      sourceType: "competitor_pricing_signal",
      dateFrom: "2026-05-01",
      dateTo: "2026-07-31",
      includeCompanyContext: true,
    }),
    {
      query: "pricing changed",
      competitor_ids: ["competitor-1"],
      categories: ["pricing"],
      source_types: ["competitor_pricing_signal"],
      observed_after: "2026-05-01T00:00:00.000Z",
      observed_before: "2026-07-31T23:59:59.999Z",
      include_company_context: true,
      limit: 12,
    },
  );
});

test("intelligence labels and dates are readable", () => {
  assert.equal(intelligenceLabel("customer_proof"), "Customer Proof");
  assert.match(evidenceDate("2026-07-01T12:00:00Z"), /1 Jul 2026/);
});
