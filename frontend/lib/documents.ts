import type { CompanyDocument } from "@/lib/api/types";

export const maximumCompanyPdfSize = 20 * 1024 * 1024;

export function validateCompanyPdf(file: {
  name: string;
  type: string;
  size: number;
}): string | null {
  if (
    file.type !== "application/pdf" &&
    !file.name.toLowerCase().endsWith(".pdf")
  ) {
    return "Choose a PDF document.";
  }
  if (file.size > maximumCompanyPdfSize) {
    return "The PDF must be 20 MB or smaller.";
  }
  return null;
}

export function onboardingDocumentIds(documentId: string | null): string[] {
  return documentId ? [documentId] : [];
}

export function documentStatusText(
  document: Pick<
    CompanyDocument,
    "status" | "stage" | "progress_percent"
  >,
): string {
  return document.status === "processing"
    ? `${document.stage.replaceAll("_", " ")} · ${document.progress_percent}%`
    : document.status;
}

export function canReprocessDocument(
  status: CompanyDocument["status"],
): boolean {
  return status === "uploaded" || status === "failed" || status === "ready";
}
