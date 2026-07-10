"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import { Check, Copy } from "lucide-react";
import { EvidenceDashboardFrame } from "@/features/evidence/EvidenceDashboardFrame";

export function MarkdownReport({
  markdown,
  defaultMode = "rendered",
  dashboardUrl,
  onSave,
  saveStatus = "idle",
  saveDisabled = false,
}: {
  markdown: string;
  defaultMode?: "rendered" | "raw" | "dashboard";
  dashboardUrl?: string | null;
  onSave?: () => Promise<void> | void;
  saveStatus?: "idle" | "saving" | "saved";
  saveDisabled?: boolean;
}) {
  const [mode, setMode] = useState<"rendered" | "raw" | "dashboard">(defaultMode);
  const [copied, setCopied] = useState(false);
  const copyTimeout = useRef<number | null>(null);

  const content = useMemo(() => markdown ?? "", [markdown]);
  const dashboardToken = "[[EVIDENCE_DASHBOARD]]";
  const parts = useMemo(() => content.split(dashboardToken), [content]);

  useEffect(() => {
    return () => {
      if (copyTimeout.current) window.clearTimeout(copyTimeout.current);
    };
  }, []);

  async function copyAll() {
    const text = content ?? "";
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const el = document.createElement("textarea");
        el.value = text;
        el.setAttribute("readonly", "true");
        el.style.position = "fixed";
        el.style.left = "-9999px";
        document.body.appendChild(el);
        el.select();
        document.execCommand("copy");
        document.body.removeChild(el);
      }

      setCopied(true);
      if (copyTimeout.current) window.clearTimeout(copyTimeout.current);
      copyTimeout.current = window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // no-op: clipboard may be blocked by browser permissions
    }
  }

  return (
    <div className="mt-2">
      <div className="mb-3 flex items-center gap-2">
        <button
          type="button"
          onClick={() => setMode("rendered")}
          className={`rounded-full border px-3 py-1 text-xs ${
            mode === "rendered" ? "border-white/20 bg-white/10 text-neutral-100" : "border-white/10 text-neutral-400 hover:bg-white/5"
          }`}
        >
          Rendered
        </button>
        <button
          type="button"
          onClick={() => setMode("raw")}
          className={`rounded-full border px-3 py-1 text-xs ${
            mode === "raw" ? "border-white/20 bg-white/10 text-neutral-100" : "border-white/10 text-neutral-400 hover:bg-white/5"
          }`}
        >
          Raw
        </button>
        <button
          type="button"
          onClick={() => setMode("dashboard")}
          className={`rounded-full border px-3 py-1 text-xs ${
            mode === "dashboard" ? "border-white/20 bg-white/10 text-neutral-100" : "border-white/10 text-neutral-400 hover:bg-white/5"
          }`}
          disabled={!dashboardUrl}
          title={dashboardUrl ? "Evidence contribution dashboard" : "Dashboard not available for this run"}
        >
          Evidence
        </button>

        {onSave ? (
          <button
            type="button"
            onClick={onSave}
            disabled={saveDisabled || saveStatus === "saving" || saveStatus === "saved"}
            className="ml-auto inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-200 hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {saveStatus === "saved" ? "Saved" : saveStatus === "saving" ? "Saving…" : "Save"}
          </button>
        ) : null}
        <button
          type="button"
          onClick={copyAll}
          title={copied ? "Copied" : "Copy report"}
          aria-label="Copy report"
          className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1 text-xs text-neutral-200 hover:bg-white/10"
        >
          {copied ? <Check className="h-3.5 w-3.5 text-emerald-300" /> : <Copy className="h-3.5 w-3.5 text-neutral-200" />}
          {copied ? "Copied" : "Copy"}
        </button>
      </div>

      {mode === "dashboard" ? (
        <div className="rounded-2xl border border-white/10 bg-white/5 p-0">
          {dashboardUrl ? (
            <EvidenceDashboardFrame src={dashboardUrl} className="min-h-[480px] w-full rounded-xl bg-transparent" />
          ) : (
            <div className="p-4 text-sm text-neutral-400">Dashboard not available for this run.</div>
          )}
        </div>
      ) : mode === "raw" ? (
        <pre className="whitespace-pre-wrap break-words rounded-2xl border border-white/10 bg-white/5 p-4 text-sm text-neutral-100/90">
          {content}
        </pre>
      ) : (
        <div className="rounded-2xl border border-white/10 bg-white/5 p-4 text-base text-neutral-100/90">
          {parts.map((part, idx) => (
            <div key={idx}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw]}
                components={{
                  h1: (props) => <h1 className="mb-3 text-xl font-semibold tracking-tight text-neutral-100" {...props} />,
                  h2: (props) => <h2 className="mb-2 mt-6 text-lg font-semibold text-neutral-100" {...props} />,
                  h3: (props) => <h3 className="mb-2 mt-5 text-base font-semibold text-neutral-100" {...props} />,
                  p: (props) => <p className="my-2 leading-relaxed text-neutral-100/90" {...props} />,
                  ul: (props) => <ul className="my-2 list-disc space-y-1 pl-5 text-neutral-100/90" {...props} />,
                  ol: (props) => <ol className="my-2 list-decimal space-y-1 pl-5 text-neutral-100/90" {...props} />,
                  li: (props) => <li className="leading-relaxed" {...props} />,
                  a: (props) => <a className="text-sky-300 hover:underline" target="_blank" rel="noreferrer" {...props} />,
                  code: (props) => <code className="rounded bg-black/40 px-1 py-0.5 text-[0.92em] text-neutral-100" {...props} />,
                  pre: (props) => <pre className="my-3 overflow-x-auto rounded-xl bg-black/40 p-3 text-xs text-neutral-100" {...props} />,
                  table: (props) => (
                    <div className="my-3 overflow-x-auto">
                      <table className="min-w-max w-full table-auto border-collapse text-sm" {...props} />
                    </div>
                  ),
                  th: (props) => (
                    <th
                      className="border-b border-white/10 bg-white/5 px-3 py-2 text-left text-xs font-semibold text-neutral-200 whitespace-nowrap"
                      {...props}
                    />
                  ),
                  td: (props) => (
                    <td className="border-b border-white/5 px-3 py-2 align-top text-sm text-neutral-100/90 whitespace-normal break-words" {...props} />
                  ),
                  blockquote: (props) => <blockquote className="my-3 border-l-2 border-white/10 pl-4 text-neutral-200/80" {...props} />,
                  hr: (props) => <hr className="my-6 border-white/10" {...props} />,
                }}
              >
                {part}
              </ReactMarkdown>
              {idx < parts.length - 1 && dashboardUrl ? (
                <div className="my-4 overflow-hidden rounded-2xl border border-white/10 bg-white/5">
                  <EvidenceDashboardFrame src={dashboardUrl} className="min-h-[480px] w-full bg-transparent" />
                </div>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
