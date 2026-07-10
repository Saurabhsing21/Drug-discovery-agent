"""
bio_context_fetcher.py
======================
Fetches biological context from two free, authoritative APIs:

  1. UniProt REST API  — protein class, molecular function, curated disease associations
  2. Reactome REST API — biological pathway membership (for conflict interpretation)

Both APIs are:
  - Completely free, no API key required
  - Maintained by EMBL-EBI (European Bioinformatics Institute)
  - Used internally by Open Targets and PHAROS themselves

These enrichments are injected into the LLM payload so reports explain
WHY scores differ (e.g. EGFR vs KRAS) instead of just reporting numbers.
They also enrich conflict rationales with pathway context so disagreements
between sources are biologically interpretable, not just "spread=0.4".

Design principles:
  - NEVER raises an exception — all failures return empty dicts/lists
  - NEVER blocks the main pipeline — 8-second timeout on every call
  - Results are additive — if an API is down, the report just loses the bonus context
"""
from __future__ import annotations

import logging
from typing import Any

try:
    import requests as _requests
    _HAS_REQUESTS = True
except ImportError:  # pragma: no cover
    _HAS_REQUESTS = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TIMEOUT = 8  # seconds — hard cap on every outbound call

# UniProt REST API — reviewed (Swiss-Prot) entries only
_UNIPROT_SEARCH = "https://rest.uniprot.org/uniprotkb/search"

# Reactome Content Service
_REACTOME_SEARCH = "https://reactome.org/ContentService/search/query"
_REACTOME_PATHWAYS = "https://reactome.org/ContentService/data/pathways/low/entity/{stId}/allForms"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None) -> Any | None:
    """
    Safe GET wrapper. Returns parsed JSON or None on any failure.
    Logs at DEBUG level so it never pollutes normal output.
    """
    if not _HAS_REQUESTS:
        return None
    try:
        resp = _requests.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("bio_context_fetcher: GET %s failed: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# UniProt — protein class + molecular function + curated diseases
# ---------------------------------------------------------------------------

def fetch_uniprot_context(gene_symbol: str, organism_id: int = 9606) -> dict:
    """
    Queries UniProt for a human gene and returns curated biological context.

    Returns a dict with:
      protein_full_name       str   — e.g. "Epidermal growth factor receptor"
      protein_family          str   — e.g. "Belongs to the protein kinase superfamily"
      molecular_function      str   — up to 600-char curated function description
      curated_disease_assocs  list  — up to 6 UniProt-curated disease names
      uniprot_accession       str   — primary UniProt accession (e.g. "P00533")

    Returns {} on any failure (API down, gene not found, network error).

    Why UniProt:
      - Every entry is manually reviewed by PhD curators at EMBL-EBI/SIB/PIR
      - Provides protein *class* (Kinase, GTPase, GPCR) which explains WHY
        tractability differs between targets — the core of Ziheng's feedback
      - Open Targets and PHAROS both cross-reference UniProt internally
    """
    data = _get(
        _UNIPROT_SEARCH,
        params={
            "query": (
                f"gene_exact:{gene_symbol} "
                f"AND organism_id:{organism_id} "
                f"AND reviewed:true"
            ),
            "fields": (
                "accession,protein_name,cc_function,"
                "cc_disease,protein_families,cc_similarity"
            ),
            "format": "json",
            "size": 1,
        },
    )

    if not data:
        return {}

    results = data.get("results", [])
    if not results:
        logger.debug("bio_context_fetcher: UniProt returned no results for %s", gene_symbol)
        return {}

    entry = results[0]

    # --- Full protein name ---
    protein_desc = entry.get("proteinDescription", {})
    rec_name = protein_desc.get("recommendedName", {})
    full_name = (rec_name.get("fullName") or {}).get("value", "")

    # --- Protein family / class (from SIMILARITY comment) ---
    family_text = ""
    for comment in entry.get("comments", []):
        if comment.get("commentType") == "SIMILARITY":
            for text_obj in comment.get("texts", []):
                v = text_obj.get("value", "")
                if v:
                    family_text = v[:300]
                    break
            if family_text:
                break

    # --- Molecular function (curator-written, gold standard) ---
    function_text = ""
    for comment in entry.get("comments", []):
        if comment.get("commentType") == "FUNCTION":
            texts = comment.get("texts", [])
            if texts:
                function_text = texts[0].get("value", "")[:600]
                break

    # --- Curated disease associations ---
    diseases: list[str] = []
    for comment in entry.get("comments", []):
        if comment.get("commentType") == "DISEASE":
            disease_info = comment.get("disease", {})
            name = (
                disease_info.get("diseaseId")
                or disease_info.get("description", "")
            )
            if name and name not in diseases:
                diseases.append(name)

    return {
        "protein_full_name": full_name,
        "protein_family": family_text,
        "molecular_function": function_text,
        "curated_disease_assocs": diseases[:6],
        "uniprot_accession": entry.get("primaryAccession", ""),
    }


# ---------------------------------------------------------------------------
# Reactome — pathway membership for conflict interpretation
# ---------------------------------------------------------------------------

def fetch_reactome_pathways(gene_symbol: str) -> list[str]:
    """
    Returns the top biological pathway names for a gene from Reactome.

    Used to enrich conflict rationales: when two sources disagree, the
    pathway context explains whether the conflict is biologically expected
    (e.g. EGFR essential in lung but not pancreatic lines because KRAS
    dominates MAPK signalling in pancreatic cancer) or a true data gap.

    Returns up to 5 pathway display names, e.g.:
      ["Signaling by EGFR in Cancer",
       "MAPK1/MAPK3 Signaling",
       "PI3K/AKT Signaling in Cancer"]

    Returns [] on any failure.

    Why Reactome:
      - Peer-reviewed, manually curated molecular reaction database at EMBL-EBI
      - The only source that tells us WHICH cellular pathway a gene operates in
      - Free REST API, no credentials needed
      - Directly answers "is this conflict disease-context-specific?"
    """
    # Step 1: Resolve gene symbol → Reactome stable ID
    search_data = _get(
        _REACTOME_SEARCH,
        params={
            "query": gene_symbol,
            "species": "Homo sapiens",
            "types": "Protein",
            "size": 1,
        },
    )

    if not search_data:
        return []

    result_groups = search_data.get("results", [])
    if not result_groups:
        return []

    entries = result_groups[0].get("entries", [])
    if not entries:
        return []

    st_id = entries[0].get("stId", "")
    if not st_id:
        return []

    # Step 2: Fetch pathways this entity participates in
    pathway_data = _get(
        _REACTOME_PATHWAYS.format(stId=st_id),
        params={"species": 9606},
    )

    if not pathway_data or not isinstance(pathway_data, list):
        return []

    return [
        p.get("displayName", "")
        for p in pathway_data[:5]
        if p.get("displayName")
    ]
