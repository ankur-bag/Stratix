import assert from "node:assert/strict";
import test from "node:test";
import {
  canReprocessDocument,
  documentStatusText,
  maximumCompanyPdfSize,
  onboardingDocumentIds,
  validateCompanyPdf,
} from "./documents.ts";

test("accepts one bounded PDF and rejects unsupported or oversized files", () => {
  assert.equal(
    validateCompanyPdf({
      name: "company-brief.pdf",
      type: "application/pdf",
      size: maximumCompanyPdfSize,
    }),
    null,
  );
  assert.equal(
    validateCompanyPdf({ name: "notes.txt", type: "text/plain", size: 10 }),
    "Choose a PDF document.",
  );
  assert.equal(
    validateCompanyPdf({
      name: "large.pdf",
      type: "application/pdf",
      size: maximumCompanyPdfSize + 1,
    }),
    "The PDF must be 20 MB or smaller.",
  );
});

test("includes only a completed server document id in onboarding", () => {
  assert.deepEqual(onboardingDocumentIds(null), []);
  assert.deepEqual(onboardingDocumentIds("document-1"), ["document-1"]);
});

test("renders user-facing document progress without provider internals", () => {
  const label = documentStatusText({
    status: "processing",
    stage: "storing_vectors",
    progress_percent: 85,
  });
  assert.equal(label, "storing vectors · 85%");
  assert.equal(label.includes("pinecone"), false);
  assert.equal(label.includes("namespace"), false);
  assert.equal(label.includes("api_key"), false);
});

test("allows process, retry, and rebuild only from stable document states", () => {
  assert.equal(canReprocessDocument("uploaded"), true);
  assert.equal(canReprocessDocument("failed"), true);
  assert.equal(canReprocessDocument("ready"), true);
  assert.equal(canReprocessDocument("processing"), false);
  assert.equal(canReprocessDocument("pending_upload"), false);
  assert.equal(canReprocessDocument("deleted"), false);
});
