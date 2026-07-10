"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Check, ChevronDown, ChevronRight, List, Loader2, Search } from "lucide-react";

import { evidenceDashboardUrl } from "@/lib/api";
import type { Snapshot } from "@/lib/types";
import { MarkdownReport } from "@/features/report/MarkdownReport";

export type ToolStepStatus = "pending" | "active" | "done" | "error";

export type SourceStep = {
  source: string;
  status: "pending" | "running" | "success" | "partial" | "failed" | "skipped";
  recordCount?: number;
};

type StageStep = { key: string; label: string; status: "pending" | "running" | "done" | "error" };

export type ToolStep = {
  key: string;
  title: string;
  status: ToolStepStatus;
  icon: ReactNode;
  summary?: ReactNode;
  content?: ReactNode;
  collapsible?: boolean;
};

function stageLabel(stage: string): string {
  switch (stage) {
    case "validate_input":
      return "Validating input";
    case "plan_collection":
      return "Planning collection";
    case "plan_review_gate":
      return "Waiting for plan approval";
    case "collect_sources_parallel":
      return "Searching sources";
    case "normalize_evidence":
      return "Normalizing evidence";
    case "verify_evidence":
      return "Verifying evidence";
    case "assess_sufficiency":
      return "Assessing evidence sufficiency";
    case "analyze_conflicts":
      return "Analyzing conflicts";
    case "build_evidence_graph":
      return "Building evidence graph";
    case "generate_explanation":
      return "Writing answer";
    case "human_review_gate":
      return "Waiting for final review";
    case "emit_dossier":
      return "Finalizing dossier";
    default:
      return stage.replaceAll("_", " ");
  }
}

function deriveStages(log: Array<{ event: string; data: Record<string, unknown> }>): StageStep[] {
  const order = [
    "validate_input",
    "plan_collection",
    "plan_review_gate",
    "collect_sources_parallel",
    "normalize_evidence",
    "verify_evidence",
    "analyze_conflicts",
    "assess_sufficiency",
    "build_evidence_graph",
    "generate_explanation",
    "supervisor_decide",
    "prepare_review_brief",
    "human_review_gate",
    "emit_dossier",
  ] as const;

  const started = new Set<string>();
  const ended = new Set<string>();
  const errored = new Set<string>();

  for (const it of log) {
    if (it.event === "stage_start") started.add(String(it.data?.stage ?? ""));
    if (it.event === "stage_end") ended.add(String(it.data?.stage ?? ""));
    if (it.event === "stage_error") errored.add(String(it.data?.stage ?? ""));
  }

  return order.map((key) => {
    if (errored.has(key)) return { key, label: stageLabel(key), status: "error" };
    if (ended.has(key)) return { key, label: stageLabel(key), status: "done" };
    if (started.has(key)) return { key, label: stageLabel(key), status: "running" };
    return { key, label: stageLabel(key), status: "pending" };
  });
}

function deriveQueryVariants(log: Array<{ event: string; data: Record<string, unknown> }>, snapshot: Snapshot | null): string[] {
  const fromSnapshot = (snapshot?.values?.plan as any)?.query_variants;
  if (Array.isArray(fromSnapshot) && fromSnapshot.every((v) => typeof v === "string")) {
    return fromSnapshot.slice(0, 12);
  }

  for (let i = log.length - 1; i >= 0; i -= 1) {
    const it = log[i];
    if (it.event !== "stage_end") continue;
    if (String(it.data?.stage ?? "") !== "plan_collection") continue;
    const variants = (it.data as any)?.update?.plan?.query_variants;
    if (Array.isArray(variants) && variants.every((v: unknown) => typeof v === "string")) {
      return variants.slice(0, 12);
    }
  }

  return [];
}

function deriveAnswerModel(
  log: Array<{ event: string; data: Record<string, unknown> }>,
): { generationMode: string | null; modelUsed: string | null } {
  for (let i = log.length - 1; i >= 0; i -= 1) {
    const it = log[i];
    if (it.event !== "agent_report") continue;
    const stage = String(it.data?.stage_name ?? "");
    if (stage !== "generate_explanation") continue;
    const generationMode = typeof it.data?.generation_mode === "string" ? String(it.data.generation_mode) : null;
    const modelUsed = typeof it.data?.model_used === "string" ? String(it.data.model_used) : null;
    return { generationMode, modelUsed };
  }
  return { generationMode: null, modelUsed: null };
}

function deriveStageErrors(log: Array<{ event: string; data: Record<string, unknown> }>): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const it of log) {
    if (it.event !== "stage_error") continue;
    const stage = String(it.data?.stage ?? "");
    if (!stage) continue;
    const error =
      typeof it.data?.error === "string"
        ? String(it.data.error)
        : typeof (it.data as any)?.error?.message === "string"
          ? String((it.data as any).error.message)
          : JSON.stringify(it.data?.error ?? it.data ?? {});
    (out[stage] ??= []).push(error);
  }
  return out;
}

function deriveSourceErrors(
  log: Array<{ event: string; data: Record<string, unknown> }>,
  snapshot: Snapshot | null,
): Array<{ source: string; status: string; message: string }> {
  const out: Array<{ source: string; status: string; message: string }> = [];
  for (const it of log) {
    if (it.event !== "source_end") continue;
    const source = String(it.data?.source ?? "");
    if (!source) continue;
    const status = String(it.data?.status ?? "unknown").toLowerCase();
    const message = typeof it.data?.error === "string" ? String(it.data.error) : "";
    if (status !== "success" || message.trim()) out.push({ source, status, message });
  }
  if (out.length) return out.slice(-12);

  const fromSnapshot: any[] = Array.isArray(snapshot?.values?.source_status) ? (snapshot?.values?.source_status as any[]) : [];
  for (const s of fromSnapshot) {
    const source = typeof s?.source === "string" ? s.source : null;
    const status = typeof s?.status === "string" ? s.status.toLowerCase() : null;
    const message = typeof s?.error_message === "string" ? s.error_message : typeof s?.error === "string" ? s.error : "";
    if (!source || !status) continue;
    if (status !== "success" || (typeof message === "string" && message.trim())) out.push({ source, status, message });
  }
  return out.slice(-12);
}

function stepIcon(status: ToolStepStatus, kind: "thinking" | "searching" | "sources" | "answer" | "generic"): ReactNode {
  const cls =
    status === "active"
      ? "text-neutral-100/90"
      : status === "done"
        ? "text-emerald-300"
        : status === "error"
          ? "text-red-300"
          : "text-neutral-500";

  const pulse = status === "active" ? "animate-pulse" : "";
  const base = `h-4 w-4 ${cls} ${pulse}`;

  if (kind === "searching") return <Search className={base} />;
  if (kind === "answer") return status === "done" ? <Check className={base} /> : <Loader2 className={`h-4 w-4 text-sky-300 animate-spin`} />;
  if (kind === "sources") return <List className={base} />;
  return <List className={base} />;
}

function sourcePill(step: SourceStep): { icon: ReactNode; tone: string } {
  switch (step.status) {
    case "running":
      return { icon: <Search className="h-3.5 w-3.5 animate-pulse text-sky-300" />, tone: "bg-white/5 text-neutral-200" };
    case "success":
      return { icon: <Check className="h-3.5 w-3.5 text-emerald-300" />, tone: "bg-white/5 text-neutral-200" };
    case "partial":
      return { icon: <Check className="h-3.5 w-3.5 text-amber-300" />, tone: "bg-white/5 text-neutral-200" };
    case "failed":
      return { icon: <Check className="h-3.5 w-3.5 text-red-300" />, tone: "bg-white/5 text-neutral-200" };
    case "skipped":
      return { icon: <Check className="h-3.5 w-3.5 text-neutral-500" />, tone: "bg-white/5 text-neutral-500" };
    default:
      return { icon: <Search className="h-3.5 w-3.5 text-neutral-500" />, tone: "bg-white/5 text-neutral-400" };
  }
}

function toolStatusFromStages(stages: StageStep[], keys: string[], runState: string): ToolStepStatus {
  const selected = stages.filter((s) => keys.includes(s.key));
  if (runState === "failed") return "error";
  if (selected.some((s) => s.status === "error")) return "error";
  if (selected.every((s) => s.status === "done")) return "done";
  if (selected.some((s) => s.status === "running")) return "active";
  return "pending";
}

function readObject(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null ? (value as Record<string, unknown>) : null;
}

function prettySourceName(value: unknown): string {
  const raw = String(value ?? "").toLowerCase();
  if (raw === "depmap") return "DepMap";
  if (raw === "pharos") return "Pharos";
  if (raw === "opentargets") return "OpenTargets";
  if (raw === "literature") return "Literature";
  return String(value ?? "");
}

export function makeAssistantToolUI({
  runState,
  currentStage,
  log,
  snapshot,
  sourceSteps,
  answer,
  sourcesUI,
  actions,
  reportActions,
}: {
  runState: "idle" | "running" | "paused" | "completed" | "failed";
  currentStage: string | null;
  log: Array<{ event: string; data: Record<string, unknown> }>;
  snapshot: Snapshot | null;
  sourceSteps: SourceStep[];
  answer: string | null;
  sourcesUI: ReactNode;
  actions?: {
    onMoreEvidence?: () => void;
    onAllSources?: () => void;
    onTightenObjective?: () => void;
  };
  reportActions?: {
    onSaveReport?: () => Promise<void> | void;
    saveStatus?: "idle" | "saving" | "saved";
    saveDisabled?: boolean;
  };
}): ToolStep[] {
  const stages = deriveStages(log);
  const queryVariants = deriveQueryVariants(log, snapshot);
  const answerModel = deriveAnswerModel(log);
  const stageErrors = deriveStageErrors(log);
  const sourceErrors = deriveSourceErrors(log, snapshot);

  const plan = readObject(snapshot?.values?.plan);
  const query = readObject(snapshot?.values?.query);
  const geneSymbol = typeof query?.gene_symbol === "string" && query.gene_symbol.trim() ? query.gene_symbol.trim() : "";
  const runtime = readObject((snapshot as any)?._runtime);

  const sourcesFromPlan = Array.isArray((plan as any)?.selected_sources) ? ((plan as any).selected_sources as unknown[]) : null;
  const sourcesFromQuery = Array.isArray((query as any)?.sources) ? ((query as any).sources as unknown[]) : null;
  const selectedSources = sourcesFromPlan ?? sourcesFromQuery ?? [];
  const sourcesLabel = selectedSources.length ? selectedSources.map(prettySourceName).join(", ") : "selected sources";

  const verification = readObject(snapshot?.values?.verification_report);
  const sufficiency = readObject(snapshot?.values?.evidence_sufficiency);
  const conflicts = snapshot?.values?.conflicts as any;

  const validateStatus = toolStatusFromStages(stages, ["validate_input"], runState);
  const planStatus = toolStatusFromStages(stages, ["plan_collection", "plan_review_gate"], runState);
  const collectStatus = toolStatusFromStages(stages, ["collect_sources_parallel"], runState);
  const normalizeStatus = toolStatusFromStages(stages, ["normalize_evidence"], runState);
  const verifyStatus = toolStatusFromStages(stages, ["verify_evidence", "assess_sufficiency"], runState);
  const conflictStatus = toolStatusFromStages(stages, ["analyze_conflicts"], runState);
  const graphStatus = toolStatusFromStages(stages, ["build_evidence_graph"], runState);
  const answerStatus = toolStatusFromStages(stages, ["generate_explanation"], runState);
  const reviewStatus = toolStatusFromStages(
    stages,
    ["supervisor_decide", "prepare_review_brief", "human_review_gate", "emit_dossier"],
    runState,
  );

  const planPreview = plan ? (
    <div className="mt-2 text-sm text-neutral-100/90">
      {typeof plan.query_intent === "string" ? <div className="leading-relaxed">{plan.query_intent}</div> : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {Array.isArray((plan as any).selected_sources)
          ? (plan as any).selected_sources.map((s: any) => (
              <span key={String(s)} className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-200">
                {String(s)}
              </span>
            ))
          : null}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {Array.isArray((plan as any).query_variants)
          ? (plan as any).query_variants.slice(0, 12).map((q: any) => (
              <span key={String(q)} className="inline-flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1 text-xs text-neutral-200">
                <Search className="h-3.5 w-3.5 text-neutral-400" />
                <span className="truncate">{String(q)}</span>
              </span>
            ))
          : null}
      </div>
    </div>
  ) : null;

  const planJson = plan ? (
    <details className="mt-3">
      <summary className="cursor-pointer select-none text-xs font-medium text-neutral-300 hover:text-neutral-100">
        Full plan JSON
      </summary>
      <pre className="mt-2 whitespace-pre-wrap break-words rounded-2xl border border-white/10 bg-white/5 p-4 text-xs text-neutral-100/90">
        {JSON.stringify(plan, null, 2)}
      </pre>
    </details>
  ) : null;

  const collectBody = (
    <div className="mt-2">
      <div className="flex flex-wrap gap-2">
        {queryVariants.length
          ? queryVariants.map((q) => (
              <span key={q} className="inline-flex items-center gap-1.5 rounded-full bg-white/5 px-3 py-1 text-xs text-neutral-200">
                <Search className="h-3.5 w-3.5 text-neutral-400" />
                <span className="truncate">{q}</span>
              </span>
            ))
          : null}

        {sourceSteps.length
          ? sourceSteps.map((s) => {
              const pill = sourcePill(s);
              return (
                <span key={s.source} className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs ${pill.tone}`}>
                  {pill.icon}
                  <span className="font-medium">{s.source}</span>
                  {typeof s.recordCount === "number" ? <span className="text-neutral-500">{s.recordCount}</span> : null}
                </span>
              );
            })
          : null}
      </div>

      {sourceErrors.length ? (
        <div className="mt-4 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
          <div className="text-xs font-semibold text-neutral-300">Source errors</div>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-neutral-200/90">
            {sourceErrors.map((e) => (
              <li key={`${e.source}:${e.status}:${e.message}`}>
                <span className="text-neutral-400">{prettySourceName(e.source)}:</span> {e.status}
                {e.message.trim() ? <span className="text-neutral-400"> — {e.message}</span> : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {Array.isArray(stageErrors.collect_sources_parallel) && stageErrors.collect_sources_parallel.length ? (
        <div className="mt-4 rounded-2xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-100/90">
          <div className="text-xs font-semibold text-red-200">Collector error</div>
          <div className="mt-2 whitespace-pre-wrap break-words text-sm">
            {stageErrors.collect_sources_parallel[stageErrors.collect_sources_parallel.length - 1]}
          </div>
        </div>
      ) : null}

      <div className="mt-4">{sourcesUI}</div>
    </div>
  );

  const verifyBody = verification || sufficiency ? (
    <div className="mt-2 space-y-3 text-sm text-neutral-100/90">
      {(() => {
        const errs = [...(stageErrors.verify_evidence ?? []), ...(stageErrors.assess_sufficiency ?? [])];
        if (!errs.length) return null;
        return (
          <div className="rounded-2xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-100/90">
            <div className="text-xs font-semibold text-red-200">Verification error</div>
            <div className="mt-2 whitespace-pre-wrap break-words text-sm">{errs[errs.length - 1]}</div>
          </div>
        );
      })()}
      {verification ? (
        <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
          <div className="text-xs font-semibold text-neutral-300">Verification</div>
          <div className="mt-2 text-sm">
            <span className={`font-medium ${verification.blocked ? "text-red-300" : "text-emerald-300"}`}>
              {verification.blocked ? "Blocked" : "Not blocked"}
            </span>
            <span className="ml-2 text-neutral-400">
              warnings: {typeof verification.warning_count === "number" ? verification.warning_count : 0}
            </span>
          </div>
          {Array.isArray(verification.blocking_issues) && verification.blocking_issues.length ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-red-200/90">
              {verification.blocking_issues.map((r: any) => (
                <li key={String(r)}>{String(r)}</li>
              ))}
            </ul>
          ) : null}
          {Array.isArray(verification.warning_issues) && verification.warning_issues.length ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-amber-200/90">
              {verification.warning_issues.map((r: any) => (
                <li key={String(r)}>{String(r)}</li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}

      {sufficiency ? (
        <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
          <div className="text-xs font-semibold text-neutral-300">Sufficiency</div>
          <div className="mt-2 text-sm">
            <span className={`font-medium ${sufficiency.sufficient ? "text-emerald-300" : "text-amber-300"}`}>
              {sufficiency.sufficient ? "Sufficient" : "Insufficient"}
            </span>
            <span className="ml-2 text-neutral-400">
              total {String(sufficiency.total_items ?? "?")} / min {String(sufficiency.min_total ?? "?")}
            </span>
          </div>
          {Array.isArray(sufficiency.reasons) && sufficiency.reasons.length ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-neutral-200/90">
              {sufficiency.reasons.map((r: any) => (
                <li key={String(r)}>{String(r)}</li>
              ))}
            </ul>
          ) : null}
          <div className="mt-2 text-xs text-neutral-400">
            Auto recollect passes: {String(snapshot?.values?.auto_recollect_count ?? 0)}
            {(() => {
              const q = readObject(snapshot?.values?.query);
              if (!q) return null;
              return (
                <span className="ml-2">
                  · limits: top_k={String(q.per_source_top_k ?? "?")} · literature={String(q.max_literature_articles ?? "?")}
                </span>
              );
            })()}
          </div>
        </div>
      ) : null}
    </div>
  ) : null;

  const conflictBody =
    Array.isArray(conflicts) && conflicts.length ? (
      <div className="mt-2 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
        <div className="text-xs font-semibold text-neutral-300">Conflicts</div>
        <div className="mt-2 text-neutral-300">Detected: {conflicts.length}</div>
        <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-neutral-200/90">
          {conflicts.slice(0, 10).map((c: any) => (
            <li key={String(c.conflict_id ?? c.rationale ?? Math.random())}>
              <span className="text-neutral-400">{String(c.severity ?? "unknown")}:</span> {String(c.rationale ?? "")}
            </li>
          ))}
        </ul>
      </div>
    ) : (
      <div className="mt-2 text-sm text-muted-foreground">No conflicts reported.</div>
    );

  const graphSnapshot = readObject(snapshot?.values?.evidence_graph);
  const graphBody = graphSnapshot ? (
    <div className="mt-2 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
      <div className="text-xs font-semibold text-neutral-300">Evidence graph</div>
      <div className="mt-2 text-neutral-300">
        nodes: {Array.isArray((graphSnapshot as any).nodes) ? (graphSnapshot as any).nodes.length : 0} · edges:{" "}
        {Array.isArray((graphSnapshot as any).edges) ? (graphSnapshot as any).edges.length : 0}
      </div>
    </div>
  ) : graphStatus === "active" ? (
    <div className="mt-2 text-sm text-muted-foreground">Creating evidence graph…</div>
  ) : null;

  const answerMeta =
    answerModel.generationMode || answerModel.modelUsed ? (
      <span className="inline-flex items-center rounded-full bg-white/5 px-2.5 py-1 text-[11px] text-neutral-300">
        {answerModel.generationMode === "deterministic_fallback" ? "Fallback" : answerModel.generationMode ?? "LLM"}
        {answerModel.modelUsed ? ` · ${answerModel.modelUsed}` : ""}
      </span>
    ) : null;

  const answerBody = answer ? (
    <div className="mt-2 text-base leading-relaxed">
      {runtime?.provider === "none" ? (
        <div className="mb-4 rounded-2xl border border-amber-500/20 bg-amber-500/5 p-4 text-sm text-amber-100/90">
          <div className="text-xs font-semibold text-amber-200">LLM disabled</div>
          <div className="mt-2 leading-relaxed">
            No reachable LLM provider was detected on backend startup; the system is running in deterministic fallback mode.
          </div>
          {typeof (runtime as any)?.error === "string" && String((runtime as any).error).trim() ? (
            <div className="mt-2 whitespace-pre-wrap break-words text-xs text-amber-200/90">{String((runtime as any).error)}</div>
          ) : null}
        </div>
      ) : null}
      {Array.isArray(stageErrors.generate_explanation) && stageErrors.generate_explanation.length ? (
        <div className="mb-4 rounded-2xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-100/90">
          <div className="text-xs font-semibold text-red-200">Report error</div>
          <div className="mt-2 whitespace-pre-wrap break-words text-sm">
            {stageErrors.generate_explanation[stageErrors.generate_explanation.length - 1]}
          </div>
        </div>
      ) : typeof (snapshot as any)?._persisted?.error === "string" && String((snapshot as any)._persisted.error).trim() ? (
        <div className="mb-4 rounded-2xl border border-red-500/20 bg-red-500/5 p-4 text-sm text-red-100/90">
          <div className="text-xs font-semibold text-red-200">Run error</div>
          <div className="mt-2 whitespace-pre-wrap break-words text-sm">{String((snapshot as any)._persisted.error)}</div>
        </div>
      ) : null}
      <MarkdownReport
        markdown={answer}
        defaultMode="rendered"
        dashboardUrl={snapshot?.run_id ? evidenceDashboardUrl(snapshot.run_id) : null}
        onSave={reportActions?.onSaveReport}
        saveStatus={reportActions?.saveStatus}
        saveDisabled={reportActions?.saveDisabled}
      />
      {actions?.onMoreEvidence || actions?.onAllSources || actions?.onTightenObjective ? (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {actions?.onMoreEvidence ? (
            <button
              type="button"
              onClick={actions.onMoreEvidence}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-neutral-200 hover:bg-white/10"
            >
              Regenerate with more evidence
            </button>
          ) : null}
          {actions?.onAllSources ? (
            <button
              type="button"
              onClick={actions.onAllSources}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-neutral-200 hover:bg-white/10"
            >
              Rerun with all sources
            </button>
          ) : null}
          {actions?.onTightenObjective ? (
            <button
              type="button"
              onClick={actions.onTightenObjective}
              className="rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-sm text-neutral-200 hover:bg-white/10"
            >
              Tighten objective
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  ) : answerStatus === "active" ? (
    <div className="mt-2 text-sm text-muted-foreground">Compiling report…</div>
  ) : null;

  const validationReport = readObject(snapshot?.values?.input_validation_report);
  const validateBody = validationReport ? (
    <div className="mt-2 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
      <div className="text-xs font-semibold text-neutral-300">Input validation</div>
      {typeof validationReport.summary === "string" ? <div className="mt-2 leading-relaxed">{validationReport.summary}</div> : null}
      {Array.isArray(snapshot?.values?.past_runs) ? (
        <div className="mt-2 text-xs text-neutral-400">Past runs found: {(snapshot?.values?.past_runs as any[]).length}</div>
      ) : null}
    </div>
  ) : null;

  const normalizationReport = readObject(snapshot?.values?.normalization_report);
  const normalizeBody = normalizationReport ? (
    <div className="mt-2 rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
      <div className="text-xs font-semibold text-neutral-300">Normalization</div>
      {typeof normalizationReport.summary === "string" ? <div className="mt-2 leading-relaxed">{normalizationReport.summary}</div> : null}
    </div>
  ) : null;

  const supervisor = readObject(snapshot?.values?.supervisor_decision);
  const reviewBrief = readObject(snapshot?.values?.review_brief);
  const finalDossier = readObject(snapshot?.values?.final_dossier);
  const reviewBody =
    supervisor || reviewBrief || finalDossier ? (
      <div className="mt-2 space-y-3 text-sm text-neutral-100/90">
        {supervisor ? (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="text-xs font-semibold text-neutral-300">Supervisor decision</div>
            <div className="mt-2 text-neutral-300">
              action: <span className="text-neutral-100">{String(supervisor.action ?? "unknown")}</span>
            </div>
            {typeof supervisor.rationale === "string" ? <div className="mt-2 leading-relaxed">{supervisor.rationale}</div> : null}
          </div>
        ) : null}
        {reviewBrief ? (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="text-xs font-semibold text-neutral-300">Review brief</div>
            {typeof reviewBrief.summary === "string" ? <div className="mt-2 leading-relaxed">{reviewBrief.summary}</div> : null}
          </div>
        ) : null}
        {finalDossier ? (
          <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
            <div className="text-xs font-semibold text-neutral-300">Dossier</div>
            <div className="mt-2 text-neutral-300">
              artifact: <span className="text-neutral-100">{String(finalDossier.artifact_path ?? "—")}</span>
            </div>
          </div>
        ) : null}
      </div>
    ) : null;

  return [
    {
      key: "validate",
      title: "Validate query",
      status: validateStatus,
      icon: stepIcon(validateStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {validateStatus === "done" ? `Validated ${geneSymbol || "inputs"}.` : `Validating ${geneSymbol || "inputs"}…`}
        </div>
      ),
      content: validateBody,
    },
    {
      key: "plan",
      title: selectedSources.length ? `Plan search (${selectedSources.length} sources)` : "Plan search",
      status: planStatus,
      icon: stepIcon(planStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {planStatus === "done" ? `Created plan for searching on ${sourcesLabel}.` : `Creating plan for searching on ${sourcesLabel}…`}
        </div>
      ),
      content: (
        <div>
          {planPreview}
          {planJson}
        </div>
      ),
    },
    {
      key: "collect",
      title: selectedSources.length ? `Search ${selectedSources.length} sources` : "Search sources",
      status: collectStatus,
      icon: stepIcon(collectStatus, "searching"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {collectStatus === "done"
            ? `Collected evidence from ${sourcesLabel}.`
            : `Searching and collecting from ${sourcesLabel}…`}
        </div>
      ),
      content: collectBody,
    },
    {
      key: "normalize",
      title: "Normalize evidence",
      status: normalizeStatus,
      icon: stepIcon(normalizeStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {normalizeStatus === "done" ? "Normalized evidence records." : "Normalizing evidence records…"}
        </div>
      ),
      content: normalizeBody,
    },
    {
      key: "verify",
      title: "Verify evidence",
      status: verifyStatus,
      icon: stepIcon(verifyStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {verifyStatus === "done"
            ? "Verified evidence quality and checked sufficiency."
            : "Verifying evidence quality and checking sufficiency…"}
        </div>
      ),
      content: verifyBody,
    },
    {
      key: "conflicts",
      title: "Check conflicts",
      status: conflictStatus,
      icon: stepIcon(conflictStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {conflictStatus === "done" ? "Searched for conflicts across sources." : "Searching for conflicts across sources…"}
        </div>
      ),
      content: conflictBody,
    },
    {
      key: "graph",
      title: "Build evidence graph",
      status: graphStatus,
      icon: stepIcon(graphStatus, "generic"),
      summary: (
        <div className="text-sm text-neutral-100/90">
          {graphStatus === "done" ? `Created evidence graph${geneSymbol ? ` for ${geneSymbol}` : ""}.` : `Creating evidence graph${geneSymbol ? ` for ${geneSymbol}` : ""}…`}
        </div>
      ),
      content: graphBody,
    },
    {
      key: "answer",
      title: "Compile report",
      status: answerStatus,
      icon: stepIcon(answerStatus, "answer"),
      summary: (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-sm text-neutral-100/90">
          <span>
            {answerStatus === "done"
              ? `Compiled therapeutic evidence report${geneSymbol ? ` for ${geneSymbol}` : ""}.`
              : `Compiling therapeutic evidence report${geneSymbol ? ` for ${geneSymbol}` : ""}…`}
          </span>
          {answerMeta}
        </div>
      ),
      content: answerBody,
    },
    {
      key: "review",
      title: "Finalize",
      status: reviewStatus,
      icon: stepIcon(reviewStatus, "generic"),
      summary: <div className="text-sm text-neutral-100/90">{reviewStatus === "done" ? "Finalized dossier artifacts." : "Finalizing dossier artifacts…"}</div>,
      content: reviewBody,
    },
  ];
}

export function AssistantToolUI({
  runState,
  currentStage,
  log,
  snapshot,
  sourceSteps,
  answer,
  sourcesUI,
  onSaveReport,
  saveStatus,
  saveDisabled,
  onMoreEvidence,
  onAllSources,
  onTightenObjective,
  onCancelRun,
}: {
  runState: "idle" | "running" | "paused" | "completed" | "failed";
  currentStage: string | null;
  log: Array<{ event: string; data: Record<string, unknown> }>;
  snapshot: Snapshot | null;
  sourceSteps: SourceStep[];
  answer: string | null;
  sourcesUI: ReactNode;
  onSaveReport?: () => Promise<void> | void;
  saveStatus?: "idle" | "saving" | "saved";
  saveDisabled?: boolean;
  onMoreEvidence?: () => void;
  onAllSources?: () => void;
  onTightenObjective?: () => void;
  onCancelRun?: () => void;
}) {
  const steps = useMemo(
    () =>
      makeAssistantToolUI({
        runState,
        currentStage,
        log,
        snapshot,
        sourceSteps,
        answer,
        sourcesUI,
        actions: {
          onMoreEvidence,
          onAllSources,
          onTightenObjective,
        },
        reportActions: {
          onSaveReport,
          saveStatus,
          saveDisabled,
        },
      }),
    [answer, currentStage, log, onAllSources, onMoreEvidence, onTightenObjective, onSaveReport, runState, saveDisabled, saveStatus, snapshot, sourceSteps, sourcesUI],
  );

  const visibleSteps = useMemo(() => {
    if (steps.length === 0) return [];
    const last = (() => {
      for (let i = steps.length - 1; i >= 0; i -= 1) {
        if (steps[i].status !== "pending") return i;
      }
      return -1;
    })();
    // Always show the first step once a run exists, even before we receive stage events.
    const maxIndex = Math.max(0, last);
    return steps.slice(0, maxIndex + 1);
  }, [steps]);

  const allKeys = useMemo(() => visibleSteps.map((s) => s.key), [visibleSteps]);

  const defaultExpanded = useMemo(() => {
    const next = new Set<string>();
    for (const step of visibleSteps) {
      if (step.status !== "pending") next.add(step.key);
    }
    return next;
  }, [visibleSteps]);

  const [expandAll, setExpandAll] = useState(true);
  const [manual, setManual] = useState(false);
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(() => new Set(allKeys));

  useEffect(() => {
    if (expandAll) {
      setExpandedKeys(new Set(allKeys));
      return;
    }
    if (!manual) setExpandedKeys(defaultExpanded);
  }, [allKeys, defaultExpanded, expandAll, manual]);

  return (
    <div className="mt-6">
      <div className="mb-6 flex items-center justify-end gap-2">
        {onCancelRun && runState === "running" ? (
          <button
            type="button"
            onClick={onCancelRun}
            className="rounded-full border border-red-400/30 bg-red-500/10 px-3 py-1 text-xs text-red-100 hover:bg-red-500/20"
          >
            Cancel run
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => {
            setManual(true);
            setExpandAll(true);
            setExpandedKeys(new Set(allKeys));
          }}
          className={`rounded-full border px-3 py-1 text-xs ${
            expandAll ? "border-white/20 bg-white/10 text-neutral-100" : "border-white/10 text-neutral-400 hover:bg-white/5"
          }`}
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={() => {
            setManual(true);
            setExpandAll(false);
            setExpandedKeys(new Set());
          }}
          className={`rounded-full border px-3 py-1 text-xs ${
            !expandAll ? "border-white/20 bg-white/10 text-neutral-100" : "border-white/10 text-neutral-400 hover:bg-white/5"
          }`}
        >
          Collapse all
        </button>
      </div>

      <ol className="flex flex-col gap-12">
      {visibleSteps.map((step, idx) => (
        <li key={step.key} className="relative pl-10">
          {idx !== visibleSteps.length - 1 ? (
            <div className="absolute left-[11px] top-7 h-full w-px bg-white/10" aria-hidden="true" />
          ) : null}
          <div className="absolute left-0 top-1 flex h-6 w-6 items-center justify-center rounded-full bg-neutral-950">
            {step.icon}
          </div>
          <div>
            <button
              type="button"
              onClick={() => {
                setManual(true);
                setExpandAll(false);
                setExpandedKeys((prev) => {
                  const next = new Set(prev);
                  if (next.has(step.key)) next.delete(step.key);
                  else next.add(step.key);
                  return next;
                });
              }}
              className="inline-flex items-center gap-1 text-base font-medium text-neutral-100 hover:text-white"
            >
              {expandedKeys.has(step.key) ? (
                <ChevronDown className="h-4 w-4 text-neutral-500" />
              ) : (
                <ChevronRight className="h-4 w-4 text-neutral-500" />
              )}
              {step.title}
            </button>

            {step.summary ? <div className="mt-2">{step.summary}</div> : null}
            {step.content ? <div className={`mt-3 ${!expandedKeys.has(step.key) ? "hidden" : ""}`}>{step.content}</div> : null}
          </div>
        </li>
      ))}
      </ol>
    </div>
  );
}
