"use client";

import { useState } from "react";

import { createRun } from "@/lib/api";
import type { SourceName } from "@/lib/types";

const ALL_SOURCES: { key: SourceName; label: string }[] = [
  { key: "depmap", label: "DepMap" },
  { key: "pharos", label: "Pharos" },
  { key: "opentargets", label: "Open Targets" },
  { key: "literature", label: "Literature" },
];

export function RunControls({ onRunId }: { onRunId: (runId: string) => void }) {
  const [gene, setGene] = useState("KRAS");
  const [disease, setDisease] = useState<string>("");
  const [objective, setObjective] = useState<string>("");
  const [sources, setSources] = useState<Set<SourceName>>(new Set(ALL_SOURCES.map((s) => s.key)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = (key: SourceName) => {
    setSources((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const onSubmit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (sources.size === 0) {
        throw new Error("Select at least one source.");
      }
      const resp = await createRun({
        gene_symbol: gene.trim(),
        disease_id: disease.trim() || undefined,
        objective: objective.trim() || undefined,
        sources: Array.from(sources),
      });
      onRunId(resp.run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start run");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-col gap-3">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-slate-600">Gene symbol</span>
            <input
              value={gene}
              onChange={(e) => setGene(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="KRAS"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-slate-600">Disease id (optional)</span>
            <input
              value={disease}
              onChange={(e) => setDisease(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="EFO:0000311"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs font-medium text-slate-600">Objective (optional)</span>
            <input
              value={objective}
              onChange={(e) => setObjective(e.target.value)}
              className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              placeholder="Prioritize evidence for tractability"
            />
          </label>
        </div>

        <div className="flex flex-wrap gap-2">
          {ALL_SOURCES.map((s) => (
            <label key={s.key} className="flex items-center gap-2 text-sm text-slate-700">
              <input type="checkbox" checked={sources.has(s.key)} onChange={() => toggle(s.key)} />
              {s.label}
            </label>
          ))}
        </div>

        <div className="flex items-center justify-between">
          <button
            onClick={onSubmit}
            disabled={busy || gene.trim().length === 0 || sources.size === 0}
            className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            {busy ? "Starting…" : "Start run"}
          </button>
          {error ? <span className="text-sm text-red-600">{error}</span> : null}
        </div>
      </div>
    </div>
  );
}
