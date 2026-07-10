"use client";

import { useEffect, useMemo, useState } from "react";

import { postPlanDecision, resumeRun } from "@/lib/api";
import type { PlanDecisionStatus, Snapshot } from "@/lib/types";

function safeJsonParse(input: string): { ok: true; value: unknown } | { ok: false; error: string } {
  try {
    return { ok: true, value: JSON.parse(input) };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "Invalid JSON" };
  }
}

export function PlanApprovalPanel({
  runId,
  snapshot,
  reviewerId,
}: {
  runId: string;
  snapshot: Snapshot | null;
  reviewerId: string;
}) {
  const plan = (snapshot?.values?.plan as unknown) ?? null;
  const initial = useMemo(() => JSON.stringify(plan ?? {}, null, 2), [plan]);
  const [planJson, setPlanJson] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  useEffect(() => {
    setPlanJson(initial);
  }, [initial]);

  const submit = async (decision: PlanDecisionStatus) => {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      let updated_plan: Record<string, unknown> | undefined;
      if (decision === "needs_changes") {
        const parsed = safeJsonParse(planJson);
        if (!parsed.ok) throw new Error(parsed.error);
        if (typeof parsed.value !== "object" || parsed.value === null) {
          throw new Error("updated_plan must be a JSON object");
        }
        updated_plan = parsed.value as Record<string, unknown>;
      }
      await postPlanDecision({
        run_id: runId,
        decision,
        reviewer_id: reviewerId,
        reason: decision === "approved" ? "Approved by UI" : undefined,
        updated_plan,
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
        <h2 className="text-sm font-semibold text-neutral-100">Plan approval</h2>
        <span className="text-xs text-neutral-400 font-mono">{runId}</span>
      </div>
      <p className="mb-3 text-sm text-neutral-300">
        Review (and optionally edit) the plan JSON, then approve to continue execution.
      </p>

      <label className="flex flex-col gap-1">
        <span className="text-xs font-medium text-neutral-300">Plan JSON</span>
        <textarea
          value={planJson}
          onChange={(e) => setPlanJson(e.target.value)}
          className="h-64 w-full rounded-xl border border-white/10 bg-white/5 p-3 font-mono text-xs text-neutral-100 placeholder:text-neutral-500"
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
          onClick={() => submit("needs_changes")}
          disabled={busy}
          className="rounded-xl bg-white px-4 py-2 text-sm font-medium text-neutral-900 disabled:opacity-50"
        >
          Apply Changes & Resume
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
