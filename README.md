# 💊 Drug Discovery Agent

<div align="center">

**Enterprise-grade, multi-agent AI system for drug-target prioritisation**  
Built on **LangGraph · MCP · OpenAI · Google Gemini · DepMap · Open Targets · Pharos · Europe PMC**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python)](https://python.org)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2%2B-purple?style=flat-square)](https://github.com/langchain-ai/langgraph)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Next.js-14-black?style=flat-square&logo=next.js)](https://nextjs.org)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED?style=flat-square&logo=docker)](https://docker.com)
[![Tests](https://img.shields.io/badge/Tests-157%20passing-brightgreen?style=flat-square)](tests/)
[![License](https://img.shields.io/badge/License-MIT-yellow?style=flat-square)](LICENSE)

<a href="https://www.loom.com/share/544e617a5a0643d7841c73dcd8930385">
  <img src="https://img.shields.io/badge/▶%20Watch_Demo-Loom-blue?style=for-the-badge&logo=loom" alt="Watch Demo on Loom" />
</a>

</div>

---

## Table of Contents

- [Why Drug Discovery Agent](#why-drug-discovery-agent)
- [Architecture Overview](#architecture-overview)
- [LangGraph Pipeline](#langgraph-pipeline)
- [Agent Layer](#agent-layer)
- [Memory System](#memory-system)
- [Scoring System](#scoring-system)
- [Data Sources (MCP Layer)](#data-sources-mcp-layer)
- [Evidence & Artifact System](#evidence--artifact-system)
- [Human-in-the-Loop Gates](#human-in-the-loop-gates)
- [REST API Reference](#rest-api-reference)
- [Quick Start](#quick-start)
- [CLI Reference](#cli-reference)
- [Configuration Reference](#configuration-reference)
- [Frontend UI](#frontend-ui)
- [Codebase Structure](#codebase-structure)
- [Running Tests](#running-tests)
- [Deployment](#deployment)
- [Example Research Results](#example-research-results)
- [Technology Stack](#technology-stack)

---

## Why Drug Discovery Agent

Traditional drug-target prioritisation requires a researcher to manually query five or more global biomedical databases, reconcile inconsistent gene identifiers, apply subjective weights to heterogeneous evidence types, and synthesise everything into a readable assessment. This process takes days per gene and is hard to reproduce.

**Drug Discovery Agent** automates the entire pipeline end-to-end:

| Feature | Drug Discovery Agent | Manual Research |
|---|---|---|
| **Traceability** | Every score point traced to a specific database record | Notes-dependent |
| **Coverage** | 4 parallel MCP-connected databases per run | Varies by analyst |
| **Reproducibility** | Deterministic scoring + procedural memory of every run | Near-zero |
| **Conflict Detection** | Automatic cross-source conflict identification & weighting | Manual |
| **Human-in-the-Loop** | Optional plan & review approval gates | Always manual |
| **Output Format** | Structured JSON dossier + LLM-compiled narrative report | Free-form text |
| **Speed** | ~2–4 minutes per gene (parallel collection) | Hours to days |

---

## Architecture Overview

The system uses an **Orchestrator-Compiler** pattern on top of **LangGraph**, separating deterministic data collection from LLM-powered synthesis:

```
┌────────────────────────────────────────────────────────────────────────┐
│                          HUMAN / UI / CLI                               │
│                CollectorRequest  ──►  CollectionPaused                  │
└──────────────────────────┬──────────────────────────────────────────────┘
                           │ run_collection_graph()
           ┌───────────────▼───────────────────────────────┐
           │              LANGGRAPH PIPELINE               │
           │                                               │
           │  ┌──────────┐  ┌──────────┐  ┌───────────┐  │
           │  │ PLANNING │→ │COLLECTION│→ │PROCESSING │  │
           │  │  stage   │  │  stage   │  │  stage    │  │
           │  └──────────┘  └──────────┘  └───────────┘  │
           │       ↑              ↑              ↓         │
           │  [Plan Gate]   [MCP Servers]  [Scoring +     │
           │  (optional)    DepMap·Pharos   Evidence Graph]│
           │                OpenTargets·    ┌───────────┐  │
           │                   EPMC         │ SYNTHESIS │  │
           │                               │  stage    │  │
           │                               └─────┬─────┘  │
           │                                     ↓         │
           │                              [Review Gate]    │
           │                               (optional)      │
           └─────────────────────────────────────┬─────────┘
                                                 │
                   ┌─────────────────────────────▼──────────┐
                   │              EVIDENCE DOSSIER           │
                   │  JSON artifact with 1:1 source tracing  │
                   │  + LLM-compiled markdown narrative      │
                   └─────────────────────────────────────────┘
```

<div align="center">
  <img src="public/system_architecture.png" alt="Drug Discovery Agent System Architecture" width="800" />
</div>

For the detailed scoring + normalisation architecture (including all formulas and conflict logic), see [`docs/architecture_diagram.md`](docs/architecture_diagram.md).

---

## LangGraph Pipeline

The pipeline is implemented as a stateful **LangGraph** directed acyclic graph in [`src/drugagent/pipeline/graph.py`](src/drugagent/pipeline/graph.py). Every node writes to a shared `CollectorState` (a typed `TypedDict`), enabling full replay and resumability.

### Stage Flow

```
START
  │
  ▼
validate_input          ← InputValidationAgent — schema check, gene normalisation
  │
  ▼
[PLAN GATE]             ← optional; raises CollectionPaused if plan approval needed
  │
  ▼
plan_collection         ← PlanningAgent — queries episodic memory, writes JSON plan
  │
  ▼
collect_sources         ← parallel MCP dispatch across DepMap, Pharos, Open Targets, EPMC
  │
  ▼
normalize_data          ← NormalisationAgent — canonical gene IDs, unit harmonisation
  │
  ▼
score_evidence          ← deterministic ScoringEngine + optional ScoringAgent (LLM overlay)
  │
  ▼
build_evidence_graph    ← generates provenance-linked JSON graph artifact
  │
  ▼
check_sufficiency       ← EvidenceSufficiency — decides pass / needs_more_evidence
  │         │
  │         └──────────────► auto-recollect loop (max 2 iterations)
  │
  ▼
synthesize_report       ← SummarizerAgent — LLM-compiled markdown dossier
  │
  ▼
[REVIEW GATE]           ← optional; raises CollectionPaused if human review needed
  │
  ▼
END  →  EvidenceDossier artifact persisted to A4T_ARTIFACT_DIR
```

### State Schema

Every node reads and writes `CollectorState` — defined in [`src/drugagent/core/state.py`](src/drugagent/core/state.py):

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Unique run identifier (UUID) |
| `gene_symbol` | `str` | Normalised HGNC gene symbol |
| `disease_context` | `str \| None` | Optional disease context |
| `sources` | `list[SourceName]` | Data sources to query |
| `collection_plan` | `CollectionPlan \| None` | Agent-generated strategy |
| `raw_evidence` | `dict[str, list[EvidenceRecord]]` | Per-source raw records |
| `normalised_evidence` | `list[EvidenceRecord]` | Harmonised records |
| `score` | `ScoringResult \| None` | Aggregate druggability score |
| `conflicts` | `list[ConflictReport]` | Cross-source conflicts |
| `dossier` | `EvidenceDossier \| None` | Final compiled dossier |
| `stage_logs` | `list[StageLog]` | Per-stage execution trace |
| `working_memory_path` | `str \| None` | Snapshot artifact path |

---

## Agent Layer

Twelve specialised LLM agents live in [`src/drugagent/agents/`](src/drugagent/agents/). Each is independently testable and can be swapped without touching the pipeline.

| Agent | File | Role |
|---|---|---|
| **SupervisorAgent** | `supervisor.py` | Orchestrates state machine transitions; escalates on high-severity conflicts |
| **PlanningAgent** | `planning_agent.py` | Calls `build_collection_plan()` — uses episodic memory to define per-source directives |
| **Planner** | `planner.py` | Deterministic fallback when LLM planning is disabled or times out |
| **NormalisationAgent** | `normalization_agent.py` | Canonical gene ID resolution, unit harmonisation across heterogeneous schemas |
| **Verifier** | `verifier.py` | Post-collection sanity checks — completeness, schema adherence, anomaly detection |
| **SummarizerAgent** | `summarizer.py` | 1,440-line synthesis engine; compiles grounded narrative report from structured evidence |
| **SummarizerValidation** | `summarizer_validation.py` | Validates synthesized claims against source citations; rejects hallucinations |
| **Reviewer** | `reviewer.py` | Assists human-in-the-loop review with pre-analysed conflict summaries |
| **FollowupAgent** | `followup.py` | Answers Q&A queries against a completed dossier using RAG over evidence |
| **QueryInterpreter** | `query_interpreter.py` | Parses free-text gene/disease queries into structured `CollectorRequest` |
| **CompareReportAgent** | `compare_report.py` | Side-by-side comparison of multiple gene dossiers with delta scoring |
| **InputValidator** | `input_validator.py` | Pre-flight validation of `CollectorRequest` before pipeline entry |

### LLM Routing

All agents use `structured_ainvoke_with_fallbacks()` in [`src/drugagent/llm/policy.py`](src/drugagent/llm/policy.py):

```
Primary model (A4T_LLM_PROVIDER)
  │   on TimeoutError / RateLimitError / APIError
  └──► Fallback: Google Gemini 2.0 Flash  (if A4T_LLM_CROSS_PROVIDER_FALLBACK=1)
         │   on failure
         └──► Deterministic fallback (rule-based output, no LLM)
```

Rate limiting, retry back-off, and concurrency are globally controlled via env vars — no code changes needed.

---

## Memory System

A 5-layer memory system ensures every run is context-aware, reproducible, and auditable. All memory layers are implemented in [`src/drugagent/memory/`](src/drugagent/memory/).

```
┌─────────────────────────────────────────────────────────────┐
│                    5-LAYER MEMORY SYSTEM                     │
│                                                             │
│  ┌──────────────┐  Written by: PlanningAgent                │
│  │  EPISODIC    │  What it stores: past runs (gene, disease,│
│  │  MEMORY      │  outcome, plan summary, sources used)     │
│  │  episodic.py │  Used by: PlanningAgent on next run       │
│  └──────────────┘                                           │
│                                                             │
│  ┌──────────────┐  Written by: every pipeline stage         │
│  │  WORKING     │  What it stores: stage-by-stage snapshots │
│  │  MEMORY      │  Used by: FollowupAgent, resume logic     │
│  │  working.py  │                                           │
│  └──────────────┘                                           │
│                                                             │
│  ┌──────────────┐  Written by: manual curation              │
│  │  SEMANTIC    │  What it stores: gene/disease aliases,    │
│  │  MEMORY      │  canonical ID maps, ontology lookups      │
│  │  semantic.py │  Used by: NormalisationAgent              │
│  └──────────────┘                                           │
│                                                             │
│  ┌──────────────┐  Written by: pipeline on completion       │
│  │  PROCEDURAL  │  What it stores: config, prompt hashes,   │
│  │  MEMORY      │  collector sequences for reproducibility  │
│  │  procedural.py│  Used by: audit, nightly provenance job  │
│  └──────────────┘                                           │
│                                                             │
│  ┌──────────────┐  Written by: content/ directory           │
│  │  CONTENT     │  What it stores: project mission docs,    │
│  │  MEMORY      │  injected into every LLM system prompt   │
│  │  content.py  │  Used by: SummarizerAgent, Reviewer       │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## Scoring System

The scoring engine ([`src/drugagent/scoring/engine.py`](src/drugagent/scoring/engine.py)) produces a deterministic aggregate druggability score from 0.0 to 1.0.

### Formula

```
final_score = Σ (source_weight × normalised_source_score)

default weights:
  DepMap CRISPR essentiality     0.35
  Open Targets disease assoc.    0.30
  Pharos TDL classification      0.20
  Europe PMC literature          0.15

normalised_source_score ∈ [0.0, 1.0]  (per-source normalisation in scoring/normalizer.py)
```

### Score Tiers

| Score Range | Tier | Recommendation |
|---|---|---|
| **0.85 – 1.00** | 🟢 Tier 1 | Immediate prioritisation — established target |
| **0.70 – 0.84** | 🟡 Tier 2 | High priority — strong multi-source evidence |
| **0.50 – 0.69** | 🟠 Tier 3 | Moderate evidence — further in-silico investigation |
| **0.30 – 0.49** | 🔴 Tier 4 | Limited evidence — deprioritise unless novel hypothesis |
| **0.00 – 0.29** | ⚫ Tier 5 | Insufficient evidence |

### Conflict Detection

The conflict analyser ([`src/drugagent/scoring/conflicts.py`](src/drugagent/scoring/conflicts.py)) flags when two or more sources disagree by more than a configurable threshold:

| Severity | Condition | Action |
|---|---|---|
| **HIGH** | Score spread > 0.6 across sources | Triggers SupervisorAgent escalation; human review gate opened |
| **MEDIUM** | Score spread 0.3–0.6 | Flagged in dossier; SummarizerAgent must address |
| **LOW** | Score spread < 0.3 | Logged only |

### DepMap Normalisation

Raw Chronos gene-effect scores (typically −3.0 to +0.5) are normalised via a sigmoid transform ([`src/drugagent/scoring/normalizer.py`](src/drugagent/scoring/normalizer.py)):

```
normalised = 1 / (1 + exp(k × (chronos + threshold)))
```

Calibration constants (`k`, `threshold`) are validated against documented KRAS, EGFR, TP53 fixtures in [`tests/test_normalizer.py`](tests/test_normalizer.py).

---

## Data Sources (MCP Layer)

All data sources are accessed via the **Model Context Protocol (MCP)** — a standardised tool-calling interface that decouples source logic from pipeline logic. Connectors live in [`src/drugagent/data/connectors/`](src/drugagent/data/connectors/).

| Source | Connector | Protocol | What it provides |
|---|---|---|---|
| **DepMap** | `connectors/depmap.py` | Local CSV (CRISPR Chronos) | Gene-effect scores across 1,000+ cancer cell lines; CRISPR essentiality |
| **Pharos** | `connectors/pharos.py` | GraphQL API | TDL classification (Tclin/Tchem/Tbio/Tdark), ligand data, target family |
| **Open Targets** | `connectors/opentargets.py` | GraphQL API | Disease-association scores (0–1), approved drugs, tractability |
| **Europe PMC** | `connectors/literature.py` | REST API | PubMed article counts, curated gene-disease co-mentions, evidence level |

### Collection Strategy

Sources are dispatched in **parallel** (configurable via `A4T_SOURCE_DISPATCH_MODE`). Failed sources degrade gracefully — they contribute a `definitive_zero` record, not a crash:

```python
# Each connector returns one of:
EvidenceRecord(type="data",           ...)   # found records
EvidenceRecord(type="definitive_zero",...)   # searched, found nothing  
EvidenceRecord(type="absence",        ...)   # source unavailable / error
```

---

## Evidence & Artifact System

Every run writes a structured set of artifacts to `A4T_ARTIFACT_DIR` (default: `artifacts/`):

```
artifacts/
├── plans/
│   └── {run_id}.collection_plan.json      ← LLM-generated collection strategy
├── dossiers/
│   └── {run_id}.evidence_dossier.json     ← complete structured evidence dossier
├── graphs/
│   └── {run_id}.evidence_graph.json       ← provenance-linked evidence graph
├── evidence_dashboards/
│   └── {run_id}.evidence_dashboard.html   ← interactive HTML evidence explorer
├── metrics/
│   └── {run_id}.metrics.json              ← latency, source coverage, conflict rates
├── health_reports/
│   └── {run_id}.health.json               ← MCP source health check results
├── review_audit/
│   └── {run_id}/                          ← timestamped review decision log
├── review_decisions/
│   └── {run_id}.review_decision.json      ← accepted / rejected / needs_more_evidence
├── working_memory/
│   └── {run_id}/                          ← per-stage state snapshots
├── episodic_memory/
│   └── ...                                ← persistent cross-run memory
└── procedural_memory/
    └── {run_id}.procedural_memory.json    ← run configuration for reproducibility
```

### Evidence Dossier Schema

```jsonc
{
  "run_id": "uuid",
  "gene_symbol": "EGFR",
  "schema_version": "1.3",
  "overall_score": 0.91,
  "score_tier": "Tier 1",
  "sources": {
    "depmap":      { "score": 0.94, "records": 12, "latency_ms": 210 },
    "pharos":      { "score": 0.88, "records": 1,  "latency_ms": 890 },
    "opentargets": { "score": 0.96, "records": 47, "latency_ms": 1240 },
    "literature":  { "score": 0.85, "records": 38, "latency_ms": 670 }
  },
  "conflicts": [],
  "evidence_records": [ ... ],  // full record list with source citations
  "narrative_report": "## EGFR Drug Target Assessment\n..."
}
```

---

## Human-in-the-Loop Gates

Two optional approval gates can be enabled via environment variables:

### Plan Approval Gate (`A4T_REQUIRE_PLAN_APPROVAL=1`)

After `plan_collection` runs, the pipeline raises `CollectionPaused` and writes the plan to `artifacts/plans/{run_id}.collection_plan.json`. The UI presents the plan to the researcher who can:

- **Approve** — pipeline continues with LLM-generated plan
- **Modify** — researcher edits source list / directives; pipeline continues with modifications
- **Reject** — pipeline terminates; run recorded as rejected

```bash
# Resume after approval via API
POST /api/runs/{run_id}/plan-decision
{ "decision": "approved" }
```

### Review Gate (`A4T_REQUIRE_REVIEW=1`)

After `synthesize_report`, if conflicts are HIGH severity (or if the gate is always-on), the pipeline pauses again. The human reviewer sees the full evidence dossier and can:

- **Approve** — dossier accepted as final; stored to `review_decisions/`
- **Reject** — run terminates; logged in `review_audit/`
- **Needs more evidence** — pipeline re-runs collection with updated directives (max 2 iterations)

---

## REST API Reference

The FastAPI application is in [`api/main.py`](api/main.py) — launch with:

```bash
uvicorn api.main:app --reload --port 8000
```

### Endpoints

#### Runs

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs` | Start a new drug-discovery run |
| `POST` | `/api/runs/from-text` | Start a run from free-text query (uses QueryInterpreter) |
| `GET` | `/api/runs/{run_id}/state` | Get current run state and stage |
| `GET` | `/api/runs/{run_id}/events` | SSE stream of real-time progress events |
| `GET` | `/api/runs/{run_id}/artifacts` | List artifacts generated by the run |
| `POST` | `/api/runs/{run_id}/resume` | Resume a paused run |
| `POST` | `/api/runs/{run_id}/cancel` | Cancel a running or paused run |

#### Human-in-the-Loop

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs/{run_id}/plan-decision` | Submit plan approval/rejection |
| `POST` | `/api/runs/{run_id}/review-decision` | Submit evidence review decision |

#### Analysis

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/runs/{run_id}/followup` | Q&A over a completed dossier |
| `POST` | `/api/compare-report` | Compare two or more gene dossiers |
| `GET` | `/api/runs/{run_id}/evidence-dashboard` | Serve interactive HTML evidence dashboard |
| `GET` | `/api/health` | Service health check |

#### Saved Runs

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/saved-runs` | List all saved runs |
| `POST` | `/api/saved-runs` | Save a run with title + tags |
| `GET` | `/api/saved-runs/{id}` | Get saved run by ID |
| `PATCH` | `/api/saved-runs/{id}` | Update title / tags |
| `DELETE` | `/api/saved-runs/{id}` | Delete saved run |

### Example: Start a Run

```bash
curl -X POST http://localhost:8000/api/runs \
  -H "Content-Type: application/json" \
  -d '{
    "gene_symbol": "KRAS",
    "disease_context": "non-small cell lung cancer",
    "sources": ["depmap", "opentargets", "pharos", "literature"]
  }'
```

```jsonc
{
  "run_id": "a3f9c2b1-...",
  "status": "started",
  "events_url": "/api/runs/a3f9c2b1-.../events"
}
```

---

## Quick Start

### Prerequisites

| Tool | Minimum Version | Purpose |
|---|---|---|
| Python | 3.10+ | Backend runtime |
| Node.js | 18+ | Frontend build |
| Docker | Latest | Containerised deployment |
| RAM | 4 GB | DepMap CSV in memory |
| Disk | 2 GB | DepMap CSV + artifacts |

### 1. Clone & Setup

```bash
git clone https://github.com/Saurabhsing21/Drug-discovery-agent.git
cd Drug-discovery-agent

# Create virtual environment
python3 -m venv venv && source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Download DepMap CRISPR dataset (~1 GB)
python3 scripts/download_depmap.py
```

### 2. Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```ini
# LLM Provider (choose one or both)
GOOGLE_API_KEY=your_gemini_key_here
OPENAI_API_KEY=your_openai_key_here

# Provider preference (google | openai)
A4T_LLM_PROVIDER=google

# Optional: disable LLM for pure deterministic mode
A4T_LLM_CALLS_ENABLED=1
A4T_REQUIRE_LLM_AGENTS=1

# Human-in-the-loop gates (0=disabled, 1=enabled)
A4T_REQUIRE_PLAN_APPROVAL=0
A4T_REQUIRE_REVIEW=0

# Artifact storage location
A4T_ARTIFACT_DIR=./artifacts
```

### 3. Run via CLI

```bash
# Quick gene analysis — saves markdown report
python3 -m cli run --gene EGFR --save-markdown

# With disease context
python3 -m cli run --gene KRAS --disease "non-small cell lung cancer"

# Enable plan approval gate
python3 -m cli run --gene BRAF --require-plan-approval

# Deterministic mode (no LLM)
A4T_LLM_CALLS_ENABLED=0 python3 -m cli run --gene TP53
```

### 4. Run via API

```bash
# Start the API server
uvicorn api.main:app --reload --port 8000

# Or with Make
make api-dev
```

### 5. Run Frontend UI

```bash
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

---

## CLI Reference

```
python3 -m cli run [OPTIONS]

Options:
  --gene TEXT                Gene symbol (HGNC) or Ensembl ID  [required]
  --disease TEXT             Disease context (optional)
  --sources TEXT             Comma-separated source list
                             (depmap,pharos,opentargets,literature)
  --save-markdown            Write narrative report to results/
  --require-plan-approval    Open plan approval gate
  --require-review           Open review gate after synthesis
  --output-format TEXT       json | markdown | compiler [default: compiler]
  --run-id TEXT              Custom run ID (UUID generated if omitted)
  --help                     Show this message and exit.

Examples:
  python3 -m cli run --gene EGFR --save-markdown
  python3 -m cli run --gene KRAS --disease "NSCLC" --sources depmap,opentargets
  python3 -m cli run --gene BRAF --output-format json
```

---

## Configuration Reference

All configuration is via environment variables (loaded from `.env`):

### LLM Settings

| Variable | Default | Description |
|---|---|---|
| `A4T_LLM_PROVIDER` | `google` | Primary LLM provider (`google` \| `openai`) |
| `A4T_LLM_CALLS_ENABLED` | `1` | Enable live LLM calls (`0` = deterministic fallback only) |
| `A4T_REQUIRE_LLM_AGENTS` | `1` | Require LLM for synthesis agents (vs. deterministic) |
| `A4T_LLM_FALLBACK_ENABLED` | `1` | Enable deterministic fallback on LLM errors |
| `A4T_LLM_CROSS_PROVIDER_FALLBACK` | `1` | Fall back to secondary provider on primary failure |
| `A4T_LLM_CONCURRENCY` | `1` | Max parallel LLM requests |
| `A4T_LLM_RPM` | `10` | Requests per minute (rate limiter) |
| `A4T_LLM_TIMEOUT_S` | `300` | LLM call timeout in seconds |
| `A4T_LLM_RETRY_ATTEMPTS` | `3` | Max retry attempts on transient errors |
| `A4T_LLM_429_BASE_DELAY_S` | `2.0` | Base back-off delay on 429 rate-limit errors |
| `A4T_LLM_429_MAX_DELAY_S` | `8.0` | Max back-off delay on 429 rate-limit errors |
| `A4T_REQUIRE_LLM_PLANNER` | `0` | Require LLM for planning (vs. deterministic planner) |

### Pipeline Settings

| Variable | Default | Description |
|---|---|---|
| `A4T_REQUIRE_PLAN_APPROVAL` | `0` | Enable human plan approval gate |
| `A4T_REQUIRE_REVIEW` | `0` | Enable human review gate after synthesis |
| `A4T_SOURCE_DISPATCH_MODE` | `sequential` | `sequential` \| `parallel` collection mode |
| `A4T_REPORT_FORMAT` | `compiler` | `compiler` (LLM narrative) \| `structured` (JSON) |
| `A4T_ARTIFACT_DIR` | `./artifacts` | Root directory for all artifact output |

### CORS / API Settings

| Variable | Default | Description |
|---|---|---|
| `A4T_UI_CORS_ENABLED` | `1` | Enable CORS middleware |
| `A4T_UI_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins |

---

## Frontend UI

The Next.js frontend ([`frontend/`](frontend/)) provides a real-time research workbench.

### Feature Layout

```
frontend/src/
├── app/                          ← Next.js app router
│   └── page.tsx                  ← Main research workbench (1,200 lines)
├── features/                     ← Domain-scoped feature components
│   ├── run/RunControls.tsx       ← Gene input, source selection, run controls
│   ├── review/
│   │   ├── PlanApprovalPanel.tsx ← Plan review & approval UI
│   │   └── ReviewDecisionPanel.tsx← Evidence review & decision UI
│   ├── report/
│   │   ├── MarkdownReport.tsx    ← Rendered LLM narrative report
│   │   ├── ReportPanel.tsx       ← Report container with actions
│   │   └── ReportPreviewPanel.tsx← Collapsible preview
│   ├── evidence/
│   │   ├── EvidenceCarousel.tsx  ← Paginated evidence record carousel
│   │   ├── EvidenceDashboardFrame.tsx← Embedded HTML evidence explorer
│   │   └── SourcesGrid.tsx       ← Source coverage grid with latency badges
│   ├── compare/
│   │   └── CompareReportPanel.tsx← Side-by-side gene comparison charts
│   └── chat/
│       ├── ChatComposer.tsx      ← Followup Q&A input composer
│       └── ChatOutputArea.tsx   ← Chat response rendering
└── components/                   ← Shared UI primitives
    ├── AgentTimeline.tsx         ← Live pipeline stage timeline
    ├── ArtifactsPanel.tsx        ← Artifact file browser
    ├── AssistantToolUI.tsx       ← LangChain tool-call visualiser
    ├── CollapsibleSection.tsx    ← Accordion wrapper
    └── EventLog.tsx              ← SSE event stream renderer
```

### Key UI Features

- **Real-time pipeline timeline** — live stage-by-stage progress via SSE
- **Plan approval workflow** — inline JSON editor for collection plan modifications
- **Evidence carousel** — paginated view of all 50+ evidence records with source citations
- **Conflict visualisation** — colour-coded conflict severity with source attribution
- **Compare mode** — bar/radar charts for head-to-head gene scoring
- **Followup Q&A** — RAG-powered Q&A over completed dossier without re-running

---

## Codebase Structure

```
Drug-discovery-agent/
│
├── src/drugagent/               ← Canonical Python package (import this)
│   ├── core/                    ← schema.py, state.py, config.py, errors.py
│   ├── memory/                  ← episodic, working, semantic, procedural, content
│   ├── observability/           ← telemetry, metrics, health, prompt_trace
│   ├── llm/                     ← policy, providers, prompts, request_builders
│   ├── data/                    ← mcp_runtime, server_manager, connectors/, mcp_servers/
│   ├── scoring/                 ← engine, schemas, conflicts, sufficiency, normalizer
│   ├── evidence/                ← graph, dossier, artifact_store, visualizer, id
│   ├── agents/                  ← 12 LLM agent implementations
│   └── pipeline/                ← graph.py (LangGraph DAG), collector_service.py
│
├── agents/                      ← Backward-compat shim layer → src/drugagent/
├── interfaces/                  ← Human-in-the-loop contracts (plan, review, run_state)
│
├── api/                         ← FastAPI application (canonical entry-point)
│   ├── main.py                  ← App factory, CORS, startup hooks
│   ├── dependencies.py          ← Shared helpers (RUN_TASKS, saved_run_payload)
│   ├── routers/                 ← health, runs, review, followup, compare, artifacts, saved_runs
│   ├── models.py                ← Pydantic request/response schemas
│   ├── event_bus.py             ← SSE event broadcasting
│   ├── db.py / db_models.py     ← SQLAlchemy models for saved runs
│   └── saved_runs.py            ← Saved run persistence
│
├── ui_api/                      ← Legacy API (still functional; shimmed to api/)
│
├── frontend/                    ← Next.js 14 research workbench
│   └── src/
│       ├── app/                 ← App router pages
│       ├── features/            ← Domain-scoped feature components
│       ├── components/          ← Shared UI primitives
│       ├── hooks/               ← Custom React hooks
│       └── lib/                 ← API client utilities
│
├── cli/                         ← python3 -m cli run … headless entry-point
├── mcps/                        ← MCP server process entry-points
│   └── connectors/              ← Database connector implementations
│
├── tests/                       ← 175+ tests; pytest + asyncio
│   └── conftest.py              ← Autouse isolation fixtures
│
├── deploy/                      ← Docker Compose production stack
├── scripts/                     ← bootstrap_dev.sh, download_depmap.py, etc.
├── docs/                        ← Architecture diagrams, scoring formula docs
├── results/                     ← Example dossiers for EGFR, BRAF, KRAS, TP53
│
├── pyproject.toml               ← Project config (deps, mypy, pytest, ruff, coverage)
├── Makefile                     ← Developer commands
└── pytest.ini                   ← Pytest config
```

---

## Running Tests

```bash
# Full suite
PYTHONPATH=src python3 -m pytest tests/ -q

# Or with Make
make test

# Unit tests only (excludes live network tests)
make test-unit

# Integration tests (connectors, saved-runs API)
make test-integration

# With coverage
PYTHONPATH=src python3 -m pytest tests/ --cov=src/drugagent --cov-report=term-missing
```

### Test Architecture

| Category | Files | Count |
|---|---|---|
| Core schema & contracts | `test_phase1_schema.py`, `test_contract_*` | 8 |
| Memory layers | `test_episodic_memory.py` | 1 |
| Scoring & normalisation | `test_conflicts.py`, `test_normalizer.py`, `test_evidence_sufficiency.py` | 9 |
| Evidence & artifacts | `test_evidence_graph.py`, `test_artifact_store.py`, `test_dossier_emitter.py` | 5 |
| Agent logic | `test_planner.py`, `test_summary_agent.py`, `test_review_gate.py` | 7 |
| Pipeline integration | `test_graph_topology.py`, `test_graph_observability.py`, `test_state_machine_e2e.py` | 10 |
| API endpoints | `test_ui_followup_api.py`, `test_ui_evidence_dashboard_api.py` | 7 |
| Connectors (live) | `test_connector_integrations.py`, `test_mcp_endpoints.py` | 11 |
| CI & provenance | `test_ci_workflows.py`, `test_nightly_provenance_audit.py` | 3 |

Test isolation is enforced by three `autouse` fixtures in `tests/conftest.py`:
- `_clean_env` — resets `A4T_*` env vars before every test
- `_isolate_artifact_dir` — redirects artifact writes to `pytest`'s `tmp_path`
- `_reset_langgraph_checkpointer` — clears `COLLECTOR_CHECKPOINTER.storage` before/after each test

---

## Deployment

### Docker Compose (Recommended)

```bash
# Production stack: API + Frontend + Nginx
cd deploy
cp .env.example .env  # fill in your API keys
docker compose up -d --build

# Or with Make
make deploy-up
```

Services:
- `api` — FastAPI on port 8000 (internal)
- `web` — Next.js frontend on port 3000 (internal)
- `nginx` — Reverse proxy on port 80/443 (public)

### Manual / Dev

```bash
# Terminal 1: API server
make api-dev
# → uvicorn api.main:app --reload --port 8000

# Terminal 2: Frontend dev server
make frontend-install
cd frontend && npm run dev
# → http://localhost:3000
```

### Makefile Commands

```
make bootstrap        Create venv + install deps
make test             Run full pytest suite
make test-unit        Unit tests only
make test-integration Integration tests
make lint             Ruff check on src/ api/ cli/ interfaces/
make typecheck        mypy on src/drugagent
make quality          Ruff + mypy + coverage gates (CI)
make api-dev          uvicorn api.main:app --reload
make frontend-build   Build Next.js production bundle
make docker-up        Start Docker Compose stack
make deploy-up        Start production deploy/ stack
```

---

## Example Research Results

Real-world dossiers generated by the system for known oncology targets:

| Gene | Tier | Score | Report |
|---|---|---|---|
| **EGFR** | 🟢 Tier 1 | 0.91 | [EGFR Dossier](results/EGFR_summary.md) |
| **BRAF** | 🟢 Tier 1 | 0.88 | [BRAF Dossier](results/BRAF_summary.md) |
| **KRAS** | 🟢 Tier 1 | *(run to generate)* | [KRAS results/](results/) |
| **TP53** | 🟠 Tier 3 | *(run to generate)* | [TP53 results/](results/) |

> **Note:** KRAS and TP53 scores vary depending on disease context. Run with `--disease "pancreatic ductal adenocarcinoma"` for higher KRAS scores.

---

## Technology Stack

| Category | Technology | Version | Role |
|---|---|---|---|
| **AI Orchestration** | LangGraph | ≥ 0.2 | Stateful DAG pipeline, checkpointing, resumability |
| **AI Orchestration** | LangChain | ≥ 0.3 | Tool-calling, agent abstractions, prompt management |
| **Tool Protocol** | MCP (Model Context Protocol) | ≥ 1.0 | Standardised LLM ↔ data source communication |
| **LLM — Primary** | Google Gemini 2.0 Flash | latest | High-throughput synthesis; 1M token context |
| **LLM — Secondary** | OpenAI GPT-4o / o1 | latest | Reasoning-heavy tasks; cross-provider fallback |
| **Backend** | FastAPI | ≥ 0.115 | Async REST API, SSE streaming, OpenAPI docs |
| **Backend** | Python | 3.10+ | Core runtime |
| **Validation** | Pydantic v2 | ≥ 2.8 | Type-safe schemas throughout |
| **Pipeline State** | LangGraph MemorySaver | — | In-process checkpointing for resume support |
| **Database** | SQLAlchemy + PostgreSQL | ≥ 2.0 | Saved run persistence |
| **Frontend** | Next.js | 14 | Research workbench UI |
| **Frontend** | TailwindCSS + shadcn/ui | — | Component styling |
| **Data — DepMap** | CSV (Chronos gene-effect) | 24Q2 | CRISPR essentiality across 1,000+ lines |
| **Data — Pharos** | GraphQL API | — | TDL classification, ligand data |
| **Data — Open Targets** | GraphQL API | — | Disease associations, approved drugs |
| **Data — Europe PMC** | REST API | — | Literature evidence |
| **Infrastructure** | Docker + Docker Compose | — | Containerised multi-service stack |
| **Reverse Proxy** | Nginx | — | SSL termination, static assets, routing |
| **Testing** | pytest + pytest-asyncio | ≥ 8.0 | 175+ tests with async and SSE support |
| **Linting** | Ruff | ≥ 0.9 | Fast Python linting + import sorting |
| **Type-checking** | mypy | ≥ 1.11 | Static type validation |

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Run tests: `make test`
4. Run lint: `make lint`
5. Submit a pull request

### Adding a New Data Source

1. Create `src/drugagent/data/connectors/your_source.py` implementing `BaseConnector`
2. Add an MCP server entry-point in `src/drugagent/data/mcp_servers/`
3. Register the source in `src/drugagent/core/schema.py` → `SourceName` enum
4. Add connector tests in `tests/test_connector_integrations.py`

---

## 👨‍💻 Author

**Saurabh Singh** ([@Saurabhsing21](https://github.com/Saurabhsing21))

---

## 📄 License

MIT © 2026 Drug Discovery Agent Contributors.

See [LICENSE](LICENSE) for full terms.
