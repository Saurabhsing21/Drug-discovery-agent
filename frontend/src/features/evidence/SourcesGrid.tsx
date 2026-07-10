"use client";

import type { Snapshot } from "@/lib/types";

type SourceCard = {
  id: string;
  title: string;
  site: string;
  href?: string;
  domain?: string;
};

function domainFromHref(href: string | undefined): string | undefined {
  if (!href) return undefined;
  try {
    return new URL(href).hostname;
  } catch {
    return undefined;
  }
}

function faviconUrl(domain: string | undefined): string | undefined {
  if (!domain) return undefined;
  return `https://www.google.com/s2/favicons?domain=${encodeURIComponent(domain)}&sz=64`;
}

function parseSourceCards(snapshot: Snapshot | null): SourceCard[] {
  const dossier = (snapshot?.values?.final_dossier as any) ?? null;
  const graph = dossier?.graph_snapshot ?? null;
  const nodes: any[] = Array.isArray(graph?.nodes) ? graph.nodes : [];

  const pubs = nodes.filter((n) => n?.node_type === "publication" && typeof n?.id === "string");
  const cards = pubs.slice(0, 24).map((n) => {
    const id: string = n.id;
    const pmidMatch = id.match(/publication:pmid:(\d+)/i);
    if (pmidMatch) {
      const pmid = pmidMatch[1];
      const href = `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`;
      return {
        id,
        title: `PMID ${pmid}`,
        site: "PubMed",
        href,
        domain: domainFromHref(href),
      };
    }

    return {
      id,
      title: id.replaceAll("publication:", ""),
      site: "Publication",
    };
  });

  return cards;
}

export function SourcesGrid({
  snapshot,
  variant = "strip",
  hideWhenEmpty = false,
}: {
  snapshot: Snapshot | null;
  variant?: "strip" | "grid";
  hideWhenEmpty?: boolean;
}) {
  const cards = parseSourceCards(snapshot);

  if (!cards.length) {
    return hideWhenEmpty ? null : <div className="mt-2 text-sm text-muted-foreground">No sources yet.</div>;
  }

  if (variant === "grid") {
    return (
      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {cards.map((c) => (
          <a
            key={c.id}
            href={c.href ?? "#"}
            target={c.href ? "_blank" : undefined}
            rel={c.href ? "noreferrer" : undefined}
            className="group flex items-center gap-3 rounded-xl bg-white/5 p-3 hover:bg-white/10"
            onClick={(e) => {
              if (!c.href) e.preventDefault();
            }}
          >
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/5">
              {faviconUrl(c.domain) ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={faviconUrl(c.domain)} alt="" className="h-4 w-4" />
              ) : (
                <div className="h-4 w-4 rounded bg-white/10" />
              )}
            </div>
            <div className="min-w-0">
              <div className="truncate text-sm font-medium text-neutral-100">{c.title}</div>
              <div className="truncate text-xs text-muted-foreground">{c.domain ?? c.site}</div>
            </div>
          </a>
        ))}
      </div>
    );
  }

  return (
    <div className="mt-3 -mx-1 flex snap-x snap-mandatory gap-3 overflow-x-auto px-1 pb-1">
      {cards.map((c) => (
        <a
          key={c.id}
          href={c.href ?? "#"}
          target={c.href ? "_blank" : undefined}
          rel={c.href ? "noreferrer" : undefined}
          className="group min-w-[220px] snap-start rounded-xl bg-white/5 p-3 hover:bg-white/10"
          onClick={(e) => {
            if (!c.href) e.preventDefault();
          }}
        >
          <div className="flex items-center gap-2">
            {faviconUrl(c.domain) ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={faviconUrl(c.domain)} alt="" className="h-4 w-4" />
            ) : (
              <div className="h-4 w-4 rounded bg-white/10" />
            )}
            <div className="truncate text-sm font-medium text-neutral-100">{c.title}</div>
          </div>
          <div className="mt-1 truncate text-xs text-muted-foreground">{c.domain ?? c.site}</div>
        </a>
      ))}
    </div>
  );
}
