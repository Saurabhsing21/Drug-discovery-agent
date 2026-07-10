"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  BarElement,
  PointElement,
  LineElement,
  RadialLinearScale,
  Filler,
  Tooltip,
  Legend,
} from "chart.js";
import { Bar, Radar } from "react-chartjs-2";

import type { SavedRunDetail } from "@/lib/types";
import { MarkdownReport } from "@/features/report/MarkdownReport";
import { postCompareReport, saveComparison } from "@/lib/api";

ChartJS.register(CategoryScale, LinearScale, BarElement, PointElement, LineElement, RadialLinearScale, Filler, Tooltip, Legend);

const SOURCE_ORDER = ["pharos", "depmap", "open_targets", "literature"] as const;
const SOURCE_LABELS: Record<string, string> = {
  pharos: "Pharos",
  depmap: "DepMap",
  open_targets: "Open Targets",
  literature: "Literature",
};

function readScore(obj: Record<string, unknown> | null | undefined, key: string): number {
  if (!obj) return 0;
  const direct = obj[key];
  if (typeof direct === "number") return direct;
  if (key === "open_targets") {
    const alt = obj["opentargets"];
    if (typeof alt === "number") return alt;
  }
  return 0;
}

function toNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function scoredTargetFrom(run: SavedRunDetail | null): Record<string, unknown> | null {
  if (!run) return null;
  return (run.scored_target as Record<string, unknown>) ?? null;
}

function sourceScores(scored: Record<string, unknown> | null) {
  const scores = (scored?.source_scores as Record<string, unknown>) ?? {};
  const weights = (scored?.weights_used as Record<string, unknown>) ?? {};
  const confidences = (scored?.source_confidences as Record<string, unknown>) ?? {};
  const weighted = SOURCE_ORDER.map((k) => readScore(scores, k) * readScore(weights, k) * 100);
  const normalized = SOURCE_ORDER.map((k) => readScore(scores, k) * 100);
  return { weighted, normalized, scores, weights, confidences };
}

function summaryNumber(run: SavedRunDetail | null, key: string): number {
  if (!run) return 0;
  const scored = scoredTargetFrom(run);
  return toNumber(scored?.[key] as number, 0);
}

function missingSources(run: SavedRunDetail | null): string[] {
  if (!run) return [];
  const scored = scoredTargetFrom(run);
  const missing = scored?.missing_sources as unknown;
  return Array.isArray(missing) ? missing.map((m) => String(m)) : [];
}

function conflictFlag(run: SavedRunDetail | null): boolean {
  if (!run) return false;
  const scored = scoredTargetFrom(run);
  return Boolean(scored?.conflict_flag);
}

function graphCounts(run: SavedRunDetail | null): { nodes: number; edges: number } {
  if (!run) return { nodes: 0, edges: 0 };
  const graph = (run.evidence_graph as Record<string, unknown>) ?? (run.final_dossier as any)?.graph_snapshot ?? null;
  const nodes = Array.isArray((graph as any)?.nodes) ? (graph as any).nodes.length : 0;
  const edges = Array.isArray((graph as any)?.edges) ? (graph as any).edges.length : 0;
  return { nodes, edges };
}

function diffValue(a: number, b: number): string {
  const delta = b - a;
  const sign = delta > 0 ? "+" : "";
  return `${sign}${delta.toFixed(2)}`;
}

export function CompareReportPanel({ runA, runB }: { runA: SavedRunDetail | null; runB: SavedRunDetail | null }) {
  const [compareMarkdown, setCompareMarkdown] = useState<string | null>(null);
  const [compareLoading, setCompareLoading] = useState(false);
  const [compareError, setCompareError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const scoredA = scoredTargetFrom(runA);
  const scoredB = scoredTargetFrom(runB);

  const scoresA = sourceScores(scoredA);
  const scoresB = sourceScores(scoredB);

  const barData = useMemo(
    () => ({
      labels: SOURCE_ORDER.map((k) => SOURCE_LABELS[k] ?? k),
      datasets: [
        {
          label: runA?.title ?? "Run A",
          data: scoresA.weighted,
          backgroundColor: "rgba(56, 189, 248, 0.6)",
          borderColor: "rgba(56, 189, 248, 1)",
          borderWidth: 1,
        },
        {
          label: runB?.title ?? "Run B",
          data: scoresB.weighted,
          backgroundColor: "rgba(244, 114, 182, 0.6)",
          borderColor: "rgba(244, 114, 182, 1)",
          borderWidth: 1,
        },
      ],
    }),
    [runA?.title, runB?.title, scoresA.weighted, scoresB.weighted]
  );

  const radarData = useMemo(
    () => ({
      labels: SOURCE_ORDER.map((k) => SOURCE_LABELS[k] ?? k),
      datasets: [
        {
          label: runA?.title ?? "Run A",
          data: scoresA.normalized,
          backgroundColor: "rgba(56, 189, 248, 0.2)",
          borderColor: "rgba(56, 189, 248, 0.9)",
          pointBackgroundColor: "rgba(56, 189, 248, 0.9)",
        },
        {
          label: runB?.title ?? "Run B",
          data: scoresB.normalized,
          backgroundColor: "rgba(244, 114, 182, 0.2)",
          borderColor: "rgba(244, 114, 182, 0.9)",
          pointBackgroundColor: "rgba(244, 114, 182, 0.9)",
        },
      ],
    }),
    [runA?.title, runB?.title, scoresA.normalized, scoresB.normalized]
  );

  const scoreA = summaryNumber(runA, "target_score");
  const scoreB = summaryNumber(runB, "target_score");
  const confA = summaryNumber(runA, "evidence_confidence");
  const confB = summaryNumber(runB, "evidence_confidence");
  const graphA = graphCounts(runA);
  const graphB = graphCounts(runB);

  const missingA = missingSources(runA);
  const missingB = missingSources(runB);
  const showAgentReport = Boolean(compareMarkdown) && !compareLoading && !compareError;

  useEffect(() => {
    let active = true;
    if (!runA || !runB) {
      setCompareMarkdown(null);
      setCompareError(null);
      setSaveSuccess(false);
      return () => {};
    }
    const reportA = runA.summary_markdown || "";
    const reportB = runB.summary_markdown || "";
    if (!reportA.trim() || !reportB.trim()) {
      setCompareMarkdown(null);
      setCompareError("Missing report text for one of the runs.");
      return () => {};
    }
    setCompareLoading(true);
    setCompareError(null);
    postCompareReport({
      title_a: runA.title,
      title_b: runB.title,
      report_a: reportA,
      report_b: reportB,
    })
      .then((resp) => {
        if (!active) return;
        setCompareMarkdown(resp.markdown);
      })
      .catch((err) => {
        if (!active) return;
        setCompareError(err instanceof Error ? err.message : "Failed to generate comparison report");
      })
      .finally(() => {
        if (active) setCompareLoading(false);
      });
    return () => {
      active = false;
    };
  }, [runA, runB]);

  if (!runA || !runB) {
    return <div className="mt-4 text-sm text-neutral-400">Select two saved runs to generate a compare report.</div>;
  }

  const handleSave = async () => {
    if (!compareMarkdown || isSaving) return;
    setIsSaving(true);
    try {
      await saveComparison({
        title: `${runA.title} vs ${runB.title}`,
        run_a_id: runA.run_id,
        run_b_id: runB.run_id,
        compare_markdown: compareMarkdown,
        data_snapshot: {
          scoresA,
          scoresB,
          metrics: {
            scoreA,
            scoreB,
            confA,
            confB,
            graphA,
            graphB,
            missingA,
            missingB,
            conflictA: conflictFlag(runA),
            conflictB: conflictFlag(runB),
          },
        },
      });
      setSaveSuccess(true);
    } catch (err) {
      setCompareError(err instanceof Error ? err.message : "Failed to save comparison.");
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="mt-4 space-y-5">
      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="text-sm font-semibold text-neutral-100">Compare Report: {runA.title} vs {runB.title}</div>
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-white/10 bg-white/5 p-3">
            <div className="text-xs text-neutral-400">Run A</div>
            <div className="text-sm font-medium text-neutral-100">{runA.title}</div>
            <div className="mt-1 text-xs text-neutral-400">Score: {(scoreA * 100).toFixed(1)} · Confidence: {(confA * 100).toFixed(1)}</div>
          </div>
          <div className="rounded-xl border border-white/10 bg-white/5 p-3">
            <div className="text-xs text-neutral-400">Run B</div>
            <div className="text-sm font-medium text-neutral-100">{runB.title}</div>
            <div className="mt-1 text-xs text-neutral-400">Score: {(scoreB * 100).toFixed(1)} · Confidence: {(confB * 100).toFixed(1)}</div>
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="text-xs font-semibold text-neutral-300">Comparison tables</div>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-xs text-neutral-200">
            <thead className="text-[11px] uppercase text-neutral-400">
              <tr>
                <th className="py-2 pr-3">Metric</th>
                <th className="py-2 pr-3">{runA.title}</th>
                <th className="py-2 pr-3">{runB.title}</th>
                <th className="py-2">Δ (B-A)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              <tr>
                <td className="py-2 pr-3">Target score</td>
                <td className="py-2 pr-3">{scoreA.toFixed(2)}</td>
                <td className="py-2 pr-3">{scoreB.toFixed(2)}</td>
                <td className="py-2">{diffValue(scoreA, scoreB)}</td>
              </tr>
              <tr>
                <td className="py-2 pr-3">Evidence confidence</td>
                <td className="py-2 pr-3">{confA.toFixed(2)}</td>
                <td className="py-2 pr-3">{confB.toFixed(2)}</td>
                <td className="py-2">{diffValue(confA, confB)}</td>
              </tr>
              <tr>
                <td className="py-2 pr-3">Missing sources (count)</td>
                <td className="py-2 pr-3">{missingA.length}</td>
                <td className="py-2 pr-3">{missingB.length}</td>
                <td className="py-2">{diffValue(missingA.length, missingB.length)}</td>
              </tr>
              <tr>
                <td className="py-2 pr-3">Conflict flag</td>
                <td className="py-2 pr-3">{conflictFlag(runA) ? "yes" : "no"}</td>
                <td className="py-2 pr-3">{conflictFlag(runB) ? "yes" : "no"}</td>
                <td className="py-2">—</td>
              </tr>
              <tr>
                <td className="py-2 pr-3">Graph nodes</td>
                <td className="py-2 pr-3">{graphA.nodes}</td>
                <td className="py-2 pr-3">{graphB.nodes}</td>
                <td className="py-2">{diffValue(graphA.nodes, graphB.nodes)}</td>
              </tr>
              <tr>
                <td className="py-2 pr-3">Graph edges</td>
                <td className="py-2 pr-3">{graphA.edges}</td>
                <td className="py-2 pr-3">{graphB.edges}</td>
                <td className="py-2">{diffValue(graphA.edges, graphB.edges)}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs text-neutral-200">
            <thead className="text-[11px] uppercase text-neutral-400">
              <tr>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">Weighted (A)</th>
                <th className="py-2 pr-3">Weighted (B)</th>
                <th className="py-2">Δ (B-A)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {SOURCE_ORDER.map((key, idx) => {
                const aVal = scoresA.weighted[idx] ?? 0;
                const bVal = scoresB.weighted[idx] ?? 0;
                return (
                  <tr key={`weighted-${key}`}>
                    <td className="py-2 pr-3">{SOURCE_LABELS[key] ?? key}</td>
                    <td className="py-2 pr-3">{aVal.toFixed(2)}</td>
                    <td className="py-2 pr-3">{bVal.toFixed(2)}</td>
                    <td className="py-2">{diffValue(aVal, bVal)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs text-neutral-200">
            <thead className="text-[11px] uppercase text-neutral-400">
              <tr>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">Confidence (A)</th>
                <th className="py-2 pr-3">Confidence (B)</th>
                <th className="py-2">Missing</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {SOURCE_ORDER.map((key) => {
                const aConf = String(scoresA.confidences?.[key] ?? "missing");
                const bConf = String(scoresB.confidences?.[key] ?? "missing");
                const missing = [aConf === "missing" ? "A" : null, bConf === "missing" ? "B" : null]
                  .filter(Boolean)
                  .join(",");
                return (
                  <tr key={`conf-${key}`}>
                    <td className="py-2 pr-3">{SOURCE_LABELS[key] ?? key}</td>
                    <td className="py-2 pr-3">{aConf}</td>
                    <td className="py-2 pr-3">{bConf}</td>
                    <td className="py-2">{missing || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full text-left text-xs text-neutral-200">
            <thead className="text-[11px] uppercase text-neutral-400">
              <tr>
                <th className="py-2 pr-3">Missing sources</th>
                <th className="py-2 pr-3">{runA.title}</th>
                <th className="py-2">{runB.title}</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              <tr>
                <td className="py-2 pr-3">List</td>
                <td className="py-2 pr-3">{missingA.join(", ") || "none"}</td>
                <td className="py-2">{missingB.join(", ") || "none"}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
          <div className="text-xs font-semibold text-neutral-300">Weighted contribution per source</div>
          <div className="mt-3">
            <Bar
              data={barData}
              options={{
                responsive: true,
                plugins: { legend: { labels: { color: "#e5e7eb" } } },
                scales: {
                  x: { ticks: { color: "#cbd5f5" }, grid: { color: "rgba(255,255,255,0.05)" } },
                  y: { ticks: { color: "#cbd5f5" }, grid: { color: "rgba(255,255,255,0.05)" }, beginAtZero: true },
                },
              }}
            />
          </div>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
          <div className="text-xs font-semibold text-neutral-300">Normalized score per source</div>
          <div className="mt-3">
            <Radar
              data={radarData}
              options={{
                responsive: true,
                plugins: { legend: { labels: { color: "#e5e7eb" } } },
                scales: {
                  r: {
                    angleLines: { color: "rgba(255,255,255,0.1)" },
                    grid: { color: "rgba(255,255,255,0.1)" },
                    pointLabels: { color: "#cbd5f5" },
                    ticks: { color: "#cbd5f5" },
                  },
                },
              }}
            />
          </div>
        </div>
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="text-xs font-semibold text-neutral-300">Per-source evidence breakdown</div>
        <div className="mt-3 space-y-3">
          {SOURCE_ORDER.map((key, idx) => {
            const aVal = scoresA.weighted[idx] ?? 0;
            const bVal = scoresB.weighted[idx] ?? 0;
            const maxVal = Math.max(aVal, bVal, 1);
            const confA = String(scoresA.confidences?.[key] ?? "missing");
            const confB = String(scoresB.confidences?.[key] ?? "missing");
            return (
              <div key={key} className="flex items-center gap-3">
                <div className="w-28 text-xs font-semibold text-neutral-200">{SOURCE_LABELS[key] ?? key}</div>
                <div className="flex-1 space-y-1">
                  <div className="flex items-center gap-2">
                    <div className="w-12 text-[11px] text-neutral-400">{runA?.title ?? "Run A"}</div>
                    <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-sky-400/70"
                        style={{ width: `${(aVal / maxVal) * 100}%` }}
                      />
                    </div>
                    <div className="w-14 text-right text-[11px] text-neutral-300">{aVal.toFixed(1)}</div>
                    <div className="w-16 text-right text-[10px] text-neutral-500">{confA}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-12 text-[11px] text-neutral-400">{runB?.title ?? "Run B"}</div>
                    <div className="relative h-2 flex-1 overflow-hidden rounded-full bg-white/10">
                      <div
                        className="h-full rounded-full bg-pink-400/70"
                        style={{ width: `${(bVal / maxVal) * 100}%` }}
                      />
                    </div>
                    <div className="w-14 text-right text-[11px] text-neutral-300">{bVal.toFixed(1)}</div>
                    <div className="w-16 text-right text-[10px] text-neutral-500">{confB}</div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="flex items-center justify-between">
          <div className="text-xs font-semibold text-neutral-300">Comparison report</div>
          {showAgentReport && (
            <button
              onClick={handleSave}
              disabled={isSaving || saveSuccess}
              className={`px-3 py-1.5 text-xs font-medium rounded-lg transition ${
                saveSuccess
                  ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 font-semibold"
                  : "bg-pink-500 hover:bg-pink-600 text-white shadow-sm"
              }`}
            >
              {saveSuccess ? "✓ Saved to Database" : isSaving ? "Saving..." : "Save Comparison"}
            </button>
          )}
        </div>
        <div className="mt-3 text-xs text-neutral-400">
          {compareLoading ? "Generating expert comparison report..." : null}
          {compareError ? compareError : null}
        </div>
        {showAgentReport ? (
          <div className="mt-3">
            <MarkdownReport markdown={compareMarkdown ?? ""} defaultMode="rendered" />
          </div>
        ) : null}
      </div>

      <div className="rounded-2xl border border-white/10 bg-white/5 p-4">
        <div className="text-xs font-semibold text-neutral-300">Full reports</div>
        <details className="mt-3 rounded-xl border border-white/10 bg-white/5 p-3">
          <summary className="cursor-pointer text-sm text-neutral-200">Run A report</summary>
          <div className="mt-3">
            <MarkdownReport markdown={runA.summary_markdown || ""} defaultMode="rendered" />
          </div>
        </details>
        <details className="mt-3 rounded-xl border border-white/10 bg-white/5 p-3">
          <summary className="cursor-pointer text-sm text-neutral-200">Run B report</summary>
          <div className="mt-3">
            <MarkdownReport markdown={runB.summary_markdown || ""} defaultMode="rendered" />
          </div>
        </details>
      </div>
    </div>
  );
}
