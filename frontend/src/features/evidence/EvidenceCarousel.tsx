"use client";

import type { Snapshot } from "@/lib/types";

type PublicationCard = {
  id: string;
  label: string;
  subtitle: string;
  href?: string;
};

function parsePublicationCards(snapshot: Snapshot | null): PublicationCard[] {
  const dossier = (snapshot?.values?.final_dossier as any) ?? null;
  const graph = dossier?.graph_snapshot ?? null;
  const nodes: any[] = Array.isArray(graph?.nodes) ? graph.nodes : [];

  const pubs = nodes.filter((n) => n?.node_type === "publication" && typeof n?.id === "string");
  const cards: PublicationCard[] = pubs.slice(0, 12).map((n) => {
    const id: string = n.id;
    const pmidMatch = id.match(/publication:pmid:(\d+)/i);
    if (pmidMatch) {
      const pmid = pmidMatch[1];
      return {
        id,
        label: "PubMed",
        subtitle: `PMID: ${pmid}`,
        href: `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`,
      };
    }
    return { id, label: "Publication", subtitle: id };
  });

  return cards;
}

export function EvidenceCarousel({ snapshot }: { snapshot: Snapshot | null }) {
  const cards = parsePublicationCards(snapshot);

  return (
    <div className="rounded-2xl border border-white/10 bg-white/5">
      <div className="flex items-center justify-between px-5 py-4">
        <h2 className="text-sm font-semibold text-neutral-100">Sources</h2>
        <span className="text-xs text-neutral-400">{cards.length ? `${cards.length} items` : "No sources yet"}</span>
      </div>
      <div className="border-t border-white/10 px-5 py-4">
        <div className="-mx-5 flex snap-x snap-mandatory gap-3 overflow-x-auto px-5 pb-2">
          {(cards.length
            ? cards
            : [
                { id: "placeholder-1", label: "Nature", subtitle: "(Example) DOI: …" },
                { id: "placeholder-2", label: "Cell", subtitle: "(Example) Structure: …" },
                { id: "placeholder-3", label: "PubMed", subtitle: "(Example) PMID: …" },
              ]
          ).map((c) => (
            <a
              key={c.id}
              href={c.href ?? "#"}
              target={c.href ? "_blank" : undefined}
              rel={c.href ? "noreferrer" : undefined}
              className="min-w-[240px] snap-start rounded-xl border border-white/10 bg-white/5 p-4 hover:bg-white/10"
              onClick={(e) => {
                if (!c.href) e.preventDefault();
              }}
            >
              <div className="text-xs font-semibold text-neutral-100">{c.label}</div>
              <div className="mt-1 text-xs text-neutral-300">{c.subtitle}</div>
              <div className="mt-2 text-[11px] text-neutral-500">Abstract snippet (placeholder)</div>
            </a>
          ))}
        </div>
      </div>
    </div>
  );
}
