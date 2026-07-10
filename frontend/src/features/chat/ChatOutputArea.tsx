"use client";

import type { Snapshot } from "@/lib/types";

function readString(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

export function ChatOutputArea({ snapshot }: { snapshot: Snapshot | null }) {
  const explanation = readString(snapshot?.values?.explanation);
  const dossierSummary = readString((snapshot?.values?.final_dossier as any)?.summary_markdown);
  const markdown = dossierSummary ?? explanation;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5">
      <div className="flex items-center justify-between px-5 py-4">
        <h2 className="text-sm font-semibold text-neutral-100">Answer</h2>
        <span className="text-xs text-neutral-400">{dossierSummary ? "final" : markdown ? "draft" : "waiting"}</span>
      </div>
      <div className="border-t border-white/10 px-5 py-4">
        <div className="flex items-start gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-full bg-white/10 text-xs font-semibold text-white">
            DA
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-xs font-semibold text-neutral-100">Assistant</div>
            <div className="mt-2 rounded-xl border border-white/10 bg-white/5 p-4 text-xs text-neutral-100">
              {markdown ? (
                <pre className="whitespace-pre-wrap break-words text-neutral-100/90">{markdown}</pre>
              ) : (
                <div className="text-neutral-400">(Waiting for generated output…)</div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
