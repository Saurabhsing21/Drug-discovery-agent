"use client";

import { useState } from "react";

import { postReviewDecision, resumeRun } from "@/lib/api";
import type { ReviewDecisionStatus, Snapshot } from "@/lib/types";

export function ReviewDecisionPanel({
  runId,
  snapshot,
  reviewerId,
}: {
  runId: string;
  snapshot: Snapshot | null;
  reviewerId: string;
}) {
  const reviewBrief = (snapshot?.values?.review_brief as unknown) ?? null;
  const [reason, setReason] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  const submit = async (decision: ReviewDecisionStatus) => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await postReviewDecision({
        run_id: runId,
        decision,
        reviewer_id: reviewerId,
        reason: reason.trim() || undefined,
      });
      await resumeRun(runId);
      setStatus("Decision recorded; run resumed.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-2xl border border-white/10 bg-neutral-950/60 p-5">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-sm font-semibold text-neutral-100">Final review</h2>
        <span className="text-xs text-neutral-400 font-mono">{runId}</span>
      </div>
      <p className="mb-3 text-sm text-neutral-300">Review the brief, then approve/reject or request more evidence.</p>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4 text-xs">
        <div className="mb-2 text-neutral-300">Review brief</div>
        <pre className="whitespace-pre-wrap break-words text-neutral-100/90">{JSON.stringify(reviewBrief, null, 2)}</pre>
      </div>

      <label className="mt-3 flex flex-col gap-1">
        <span className="text-xs font-medium text-neutral-300">Reason (optional)</span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="h-24 w-full rounded-xl border border-white/10 bg-white/5 p-3 text-sm text-neutral-100 placeholder:text-neutral-500"
          placeholder="Why approve/reject?"
        />
      </label>

      <div className="mt-3 flex flex-wrap gap-2">
        <button
          onClick={() => submit("approved")}
          disabled={busy}
          className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-medium text-neutral-950 disabled:opacity-50"
        >
          Approve & Resume
        </button>
        <button
          onClick={() => submit("needs_more_evidence")}
          disabled={busy}
          className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-neutral-900 disabled:opacity-50"
        >
          Needs More Evidence
        </button>
        <button
          onClick={() => submit("rejected")}
          disabled={busy}
          className="rounded-xl bg-red-500 px-4 py-2 text-sm font-medium text-neutral-950 disabled:opacity-50"
        >
          Reject
        </button>
      </div>

      {error ? <div className="mt-3 text-sm text-red-300">{error}</div> : null}
      {status ? <div className="mt-3 text-sm text-emerald-300">{status}</div> : null}
    </div>
  );
}
