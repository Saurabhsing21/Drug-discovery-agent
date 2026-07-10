"use client";

import type { Snapshot } from "@/lib/types";

export function ReportPanel({ snapshot }: { snapshot: Snapshot | null }) {
  const dossier = (snapshot?.values?.final_dossier as unknown) ?? null;
  const handoff = (snapshot?.values?.final_dossier as any)?.handoff_payload as unknown;
  const markdown =
    (snapshot?.values?.final_dossier as any)?.summary_markdown ??
    (snapshot?.values?.explanation as any) ??
    null;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="mb-2 text-sm font-semibold text-slate-900">Report / Artifacts</h2>
      <div className="grid grid-cols-1 gap-3">
        {typeof markdown === "string" && markdown.trim().length ? (
          <div className="rounded-md border border-slate-100 bg-slate-50 p-3 text-xs">
            <div className="mb-2 text-slate-700">Report</div>
            <pre className="whitespace-pre-wrap break-words text-slate-700">{markdown}</pre>
          </div>
        ) : null}
        <div className="rounded-md border border-slate-100 bg-slate-50 p-3 text-xs">
          <div className="mb-2 text-slate-700">Summary</div>
          <pre className="whitespace-pre-wrap break-words text-slate-700">
            {JSON.stringify({ handoff_payload: handoff }, null, 2)}
          </pre>
        </div>
        <div className="rounded-md border border-slate-100 bg-slate-50 p-3 text-xs">
          <div className="mb-2 text-slate-700">Final dossier</div>
          <pre className="whitespace-pre-wrap break-words text-slate-700">
            {JSON.stringify(dossier, null, 2)}
          </pre>
        </div>
      </div>
    </div>
  );
}
