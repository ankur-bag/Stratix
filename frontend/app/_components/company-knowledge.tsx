"use client";

import { ChangeEvent, useEffect, useRef, useState } from "react";
import { useOrganization } from "@clerk/nextjs";
import { ApiError } from "@/lib/api/client";
import type { CompanyDocument } from "@/lib/api/types";
import { useAuthenticatedApi } from "@/lib/api/use-api";
import {
  canReprocessDocument,
  documentStatusText,
  validateCompanyPdf,
} from "@/lib/documents";

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, "0")).join("");
}

function formatBytes(bytes: number): string {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(new Date(value));
}

function friendlyWarning(code: string): string {
  const messages: Record<string, string> = {
    DOCUMENT_PARTIAL_TEXT: "Some pages did not contain extractable text.",
    DOCUMENT_REPROCESS_FAILED: "The latest rebuild failed; the previous searchable version remains active.",
    EMBEDDING_DISABLED: "Semantic search is not enabled for this environment.",
    VECTOR_SEARCH_DISABLED: "Semantic search is not enabled for this environment.",
    VECTOR_SEARCH_DEGRADED: "The document is ready, but semantic search is temporarily unavailable.",
  };
  return messages[code] ?? "Document processing completed with a warning.";
}

export function CompanyKnowledge() {
  const { isLoaded, organization } = useOrganization();
  if (!isLoaded) return <p className="mt-10 text-sm text-zinc-500">Loading company knowledge…</p>;
  if (!organization) return null;
  return <ActiveCompanyKnowledge key={organization.id} />;
}

function ActiveCompanyKnowledge() {
  const api = useAuthenticatedApi();
  const [documents, setDocuments] = useState<CompanyDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const uploadController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const response = await api.listCompanyDocuments(controller.signal);
        setDocuments(response.items);
        setLoading(false);
        if (response.items.some((item) => item.status === "processing")) {
          timer = setTimeout(load, 1500);
        }
      } catch (error) {
        if (!(error instanceof ApiError && (error.kind === "cancelled" || error.kind === "organization_switch"))) {
          setMessage(error instanceof ApiError ? error.message : "Company documents could not be loaded.");
          setLoading(false);
        }
      }
    }
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [api, refreshKey]);

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || busy) return;
    setMessage(null);
    const validationMessage = validateCompanyPdf(file);
    if (validationMessage) {
      setMessage(validationMessage);
      return;
    }
    setBusy(true);
    const controller = new AbortController();
    uploadController.current = controller;
    try {
      const upload = await api.createDocumentUpload(file);
      try {
        await api.uploadDocumentContent(upload.upload, file, controller.signal);
        const document = await api.completeDocumentUpload(upload.document.id, await sha256(file), file.size);
        const job = await api.reprocessCompanyDocument(document.id);
        setDocuments((current) => [{ ...document, status: "processing", ingestion_job_id: job.id }, ...current]);
        setRefreshKey((value) => value + 1);
      } catch (error) {
        await api.deleteCompanyDocument(upload.document.id).catch(() => undefined);
        throw error;
      }
    } catch (error) {
      if (!(error instanceof ApiError && error.kind === "cancelled")) {
        setMessage(error instanceof ApiError ? error.message : "The PDF could not be uploaded.");
      }
    } finally {
      uploadController.current = null;
      setBusy(false);
    }
  }

  async function reprocess(documentId: string) {
    setMessage(null);
    try {
      await api.reprocessCompanyDocument(documentId);
      setRefreshKey((value) => value + 1);
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Document processing could not be restarted.");
    }
  }

  async function remove(documentId: string) {
    if (!window.confirm("Delete this private PDF and its company knowledge?")) return;
    setMessage(null);
    try {
      await api.deleteCompanyDocument(documentId);
      setDocuments((current) => current.filter((item) => item.id !== documentId));
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "The document could not be deleted.");
    }
  }

  return (
    <section className="mt-10 rounded-2xl border border-black/10 bg-white p-6 shadow-sm dark:border-white/10 dark:bg-zinc-950">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold">Company knowledge</h2>
          <p className="mt-1 max-w-xl text-sm text-zinc-500">Private PDFs add evidence to company intelligence and semantic search. Text-based PDFs only, up to 20 MB and 100 pages.</p>
        </div>
        <div className="flex shrink-0 gap-2">
          {busy ? <button className="rounded-full border border-red-200 px-4 py-2 text-sm font-medium text-red-600 dark:border-red-900" onClick={() => uploadController.current?.abort()} type="button">Cancel upload</button> : null}
          <label className={`rounded-full bg-foreground px-5 py-2.5 text-sm font-medium text-background ${busy ? "cursor-not-allowed opacity-50" : "cursor-pointer"}`}>
            {busy ? "Uploading…" : "Add or replace PDF"}
            <input accept="application/pdf,.pdf" className="sr-only" disabled={busy} onChange={chooseFile} type="file" />
          </label>
        </div>
      </div>
      {message ? <p className="mt-5 rounded-lg bg-red-50 px-4 py-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300" role="alert">{message}</p> : null}
      <div className="mt-6 grid gap-3">
        {loading ? <p className="py-8 text-sm text-zinc-500">Loading company knowledge…</p> : null}
        {!loading && documents.length === 0 ? (
          <div className="rounded-xl border border-dashed border-black/15 px-5 py-8 text-center dark:border-white/15">
            <p className="font-medium">No private documents yet</p>
            <p className="mt-1 text-sm text-zinc-500">Add a company brief, pitch deck export, or product document as a PDF.</p>
          </div>
        ) : null}
        {documents.map((document) => (
          <article className="rounded-xl border border-black/10 p-4 dark:border-white/10" key={document.id}>
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div className="min-w-0">
                <p className="truncate font-medium">{document.original_filename}</p>
                <p className="mt-1 text-xs text-zinc-500">{formatDate(document.created_at)} · {formatBytes(document.size_bytes)}{document.page_count ? ` · ${document.page_count} pages` : ""}{document.chunk_count ? ` · ${document.chunk_count} chunks` : ""}</p>
                <p className="mt-2 text-sm capitalize">{documentStatusText(document)}</p>
              </div>
              <div className="flex shrink-0 gap-2">
                {canReprocessDocument(document.status) ? <button className="rounded-full border border-black/15 px-3 py-1.5 text-sm dark:border-white/15" onClick={() => void reprocess(document.id)} type="button">{document.status === "uploaded" ? "Process" : document.status === "failed" ? "Retry" : "Rebuild"}</button> : null}
                <button className="rounded-full border border-red-200 px-3 py-1.5 text-sm text-red-600 dark:border-red-900 dark:text-red-400" onClick={() => void remove(document.id)} type="button">Delete</button>
              </div>
            </div>
            {document.status === "processing" ? <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-zinc-100 dark:bg-zinc-800"><div className="h-full rounded-full bg-zinc-900 transition-[width] dark:bg-zinc-100" style={{ width: `${document.progress_percent}%` }} /></div> : null}
            {document.error_message ? <p className="mt-3 text-sm text-red-600 dark:text-red-400">{document.error_message}</p> : null}
            {document.warning_codes.map((warning) => <p className="mt-2 text-sm text-amber-700 dark:text-amber-300" key={warning}>{friendlyWarning(warning)}</p>)}
          </article>
        ))}
      </div>
    </section>
  );
}
