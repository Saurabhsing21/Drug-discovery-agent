"use client";

import type { Snapshot } from "@/lib/types";

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function ReportPreviewPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const explanation = readString(snapshot?.values?.explanation);
  const dossierSummary = readString((snapshot?.values?.final_dossier as any)?.summary_markdown);

  const markdown = dossierSummary ?? explanation;
  if (!markdown) return null;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-900">Report (preview)</h2>
        <span className="text-xs text-slate-500">{dossierSummary ? "final" : "draft"}</span>
      </div>
      <p className="mb-3 text-sm text-slate-600">
        This is the generated report text. If the run is paused for review, approve/reject to continue to dossier emission.
      </p>
      <div className="max-h-[50vh] overflow-auto rounded-md border border-slate-100 bg-slate-50 p-3 text-xs text-slate-800">
        <pre className="whitespace-pre-wrap break-words">{markdown}</pre>
      </div>
    </div>
  );
}

