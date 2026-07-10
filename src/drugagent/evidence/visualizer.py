"""
src/drugagent/evidence/visualizer.py

Generate a self-contained HTML evidence dashboard showing how each
MCP source (PHAROS, DepMap, Open Targets, Literature) contributes to
the final NormalizationScoringAgent result.

Usage:
    from drugagent.evidence.visualizer import generate_evidence_html
    generate_evidence_html([scored_target_1, scored_target_2], "artifacts/evidence.html")
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Union
from drugagent.scoring.schemas import ScoredTarget


# ─── HTML template ────────────────────────────────────────────────────────────
# The __GENES_DATA__ placeholder is replaced with the JSON payload at render time.

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Evidence Contribution Dashboard — Agent4Target</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  html, body { height: 100%; }
  body {
    font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
    background: #0A0A0A;
    color: #e6e6e6;
    font-size: 14px;
    line-height: 1.5;
    overflow: hidden;
  }
  .page { width: 100%; margin: 0 auto; padding: 28px 32px 48px; }

  /* ── header ── */
  .header { margin-bottom: 24px; border-bottom: 1px solid #1f242e; padding-bottom: 16px; }
  .header h1 { font-size: 20px; font-weight: 600; color: #f3f4f6; }
  .header p  { font-size: 13px; color: #a1a1aa; margin-top: 4px; }

  /* ── gene tabs ── */
  .gene-tabs { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
  .gene-tab {
    padding: 6px 16px;
    border: 1px solid #2a2f38;
    border-radius: 20px;
    font-size: 13px;
    cursor: pointer;
    background: #0d1117;
    color: #cbd5f5;
    transition: all .15s;
  }
  .gene-tab:hover  { background: #141b26; }
  .gene-tab.active { background: #1a2230; color: #ffffff; border-color: #2f3a63; }

  /* ── metric cards ── */
  .summary-row {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .metric-card {
    background: #161B22;
    border: 1px solid #1f242e;
    border-radius: 12px;
    padding: 14px 16px;
  }
  .metric-label { font-size: 11px; color: #a1a1aa; margin-bottom: 4px; }
  .metric-value { font-size: 22px; font-weight: 700; color: #ffffff; }
  .metric-sub   { font-size: 11px; color: #71717a; margin-top: 3px; }

  /* ── badges ── */
  .badge {
    display: inline-block;
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 999px;
  }
  .badge-ok   { background: rgba(46, 160, 67, 0.15); color: #8bd4a1; }
  .badge-warn { background: rgba(245, 158, 11, 0.2); color: #f6d28a; }
  .badge-low  { background: rgba(239, 68, 68, 0.18); color: #f6b3b3; }
  .badge-miss { background: rgba(100, 116, 139, 0.18); color: #cbd5f5; }

  /* ── conflict box ── */
  .conflict-box {
    background: #161B22;
    border: 1px solid #1f242e;
    border-top: 2px solid #f59e0b;
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 20px;
    font-size: 13px;
    color: #f5d28a;
    box-shadow: 0 0 24px rgba(245, 158, 11, 0.08);
  }
  .conflict-box strong { font-weight: 500; }

  /* ── source breakdown table ── */
  .full-card {
    background: #161B22;
    border: 1px solid #1f242e;
    border-radius: 12px;
    padding: 16px 18px;
    margin-bottom: 20px;
  }
  .section-label {
    font-size: 11px;
    font-weight: 600;
    color: #8b90a1;
    text-transform: uppercase;
    letter-spacing: .06em;
    margin-bottom: 12px;
  }
  .source-row {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 10px 0;
    border-bottom: 1px solid #1f242e;
  }
  .source-row:last-of-type { border-bottom: none; }
  .source-name  { width: 110px; font-size: 13px; font-weight: 600; color: #e5e7eb; flex-shrink: 0; }
  .bar-track    { flex: 1; height: 6px; background: #0f141b; border-radius: 999px; overflow: hidden; }
  .bar-fill     { height: 100%; border-radius: 999px; }
  .bar-label    { width: 90px; font-size: 12px; color: #a1a1aa; text-align: right; flex-shrink: 0; }
  .conf-badge   { width: 60px; font-size: 11px; text-align: center; padding: 2px 6px; border-radius: 8px; flex-shrink: 0; }
  .contrib-cell { width: 80px; font-size: 12px; color: #cbd5f5; text-align: right; flex-shrink: 0; }

  /* ── charts ── */
  .charts-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 20px;
  }
  .chart-card {
    background: #161B22;
    border: 1px solid #1f242e;
    border-radius: 12px;
    padding: 16px;
  }
  .chart-title { font-size: 13px; font-weight: 600; color: #e5e7eb; margin-bottom: 12px; }

  /* ── notes ── */
  .notes-list { list-style: none; padding: 0; margin-top: 12px; }
  .notes-list li {
    font-size: 12px;
    color: #a1a1aa;
    padding: 4px 0 4px 16px;
    position: relative;
  }
  .notes-list li::before { content: "•"; position: absolute; left: 0; color: #6b7280; }
  .notes-list .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }

  /* ── footer ── */
  .footer {
    margin-top: 24px;
    padding-top: 14px;
    border-top: 1px solid #1f242e;
    font-size: 11px;
    color: #6b7280;
  }

  @media (max-width: 640px) {
    .summary-row { grid-template-columns: 1fr 1fr; }
    .charts-grid { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="page">

  <div class="header">
    <h1>Evidence contribution dashboard</h1>
    <p>Agent4Target &mdash; Normalization &amp; Scoring Agent output</p>
  </div>

  <div class="gene-tabs" id="tabs"></div>
  <div id="conflict-area"></div>
  <div class="summary-row" id="summary-row"></div>

  <div class="full-card">
    <div class="section-label">Per-source evidence breakdown</div>
    <div id="source-rows"></div>
  </div>

  <div class="charts-grid">
    <div class="chart-card">
      <div class="chart-title">Weighted contribution to final score</div>
      <div style="position:relative;height:220px"><canvas id="waterfall"></canvas></div>
    </div>
    <div class="chart-card">
      <div class="chart-title">Normalized score per source (radar)</div>
      <div style="position:relative;height:220px"><canvas id="radar"></canvas></div>
    </div>
  </div>

  <div class="footer">
    Score version: 1.0 &nbsp;&middot;&nbsp;
    Weights: PHAROS 0.30 &middot; DepMap 0.30 &middot; Open Targets 0.25 &middot; Literature 0.15
    (rebalanced when a source is missing)
  </div>

</div>

<script>
const GENES = __GENES_DATA__;
let notifyHeightTimer = null;
function notifyHeight() {
  if (!window.parent || window.parent === window) return;
  const height = Math.max(
    document.documentElement.scrollHeight,
    document.body.scrollHeight,
    document.documentElement.offsetHeight,
    document.body.offsetHeight,
  );
  window.parent.postMessage({ type: "evidence-dashboard-height", height }, "*");
}
function scheduleNotifyHeight() {
  if (notifyHeightTimer) clearTimeout(notifyHeightTimer);
  notifyHeightTimer = setTimeout(notifyHeight, 60);
}

const SOURCE_ORDER  = ['pharos', 'depmap', 'open_targets', 'literature'];
const SOURCE_LABELS = {
  pharos: 'PHAROS', depmap: 'DepMap',
  open_targets: 'Open Targets', literature: 'Literature'
};
const SOURCE_COLORS = {
  pharos: '#6366F1',      // Vivid Indigo
  depmap: '#64748B',      // Muted Slate
  open_targets: '#3B82F6', // Electric Blue
  literature: '#F59E0B'   // Amber/Gold
};
const CONF_STYLES = {
  high:    { bg: 'rgba(34, 197, 94, 0.18)', color: '#86efac' },
  medium:  { bg: 'rgba(245, 158, 11, 0.20)', color: '#fcd34d' },
  low:     { bg: 'rgba(239, 68, 68, 0.20)', color: '#fca5a5' },
  missing: { bg: 'rgba(100, 116, 139, 0.20)', color: '#cbd5f5' }
};

let waterfallChart = null;
let radarChart = null;

function render(gene) {
  const d = GENES[gene];

  document.getElementById('conflict-area').innerHTML = d.conflict_flag
    ? '<div class="conflict-box"><strong>Conflict detected:</strong> ' + d.conflict_detail + '</div>'
    : '';

  const confScore = d.evidence_confidence;
  const confLabel = confScore >= 0.8 ? 'high' : confScore >= 0.5 ? 'medium' : 'low';
  const confStyle = CONF_STYLES[confLabel];

  document.getElementById('summary-row').innerHTML =
    '<div class="metric-card">' +
      '<div class="metric-label">Target score</div>' +
      '<div class="metric-value">' + d.target_score.toFixed(3) + '</div>' +
      '<div class="metric-sub">0 = no evidence · 1 = strong target</div>' +
    '</div>' +
    '<div class="metric-card">' +
      '<div class="metric-label">Evidence confidence</div>' +
      '<div class="metric-value" style="color:#ffffff">' + Math.round(d.evidence_confidence * 100) + '%</div>' +
      '<div class="metric-sub">Sources with data: ' + (4 - d.missing_sources.length) + ' / 4</div>' +
    '</div>' +
    '<div class="metric-card">' +
      '<div class="metric-label">Conflict flag</div>' +
      '<div class="metric-value" style="font-size:15px;padding-top:6px">' +
        (d.conflict_flag
          ? '<span class="badge badge-warn">Conflict</span>'
          : '<span class="badge badge-ok">Clean</span>') +
      '</div>' +
      '<div class="metric-sub">' + (d.conflict_flag ? 'Sources disagree' : 'Sources consistent') + '</div>' +
    '</div>' +
    '<div class="metric-card">' +
      '<div class="metric-label">Missing sources</div>' +
      '<div class="metric-value" style="font-size:15px;padding-top:6px">' +
        (d.missing_sources.length === 0
          ? '<span class="badge badge-ok">None</span>'
          : d.missing_sources.map(function(s) {
              return '<span class="badge badge-miss" style="margin-right:3px">' + (SOURCE_LABELS[s] || s) + '</span>';
            }).join('')) +
      '</div>' +
      '<div class="metric-sub">' + (d.missing_sources.length > 0 ? 'Weights rebalanced' : 'All sources active') + '</div>' +
    '</div>';

  var rowsHtml = '';
  SOURCE_ORDER.forEach(function(s) {
    var score = d.source_scores[s];
    var conf  = d.source_confidences[s];
    var w     = d.weights_used[s] || 0;
    var contribution = score != null ? score * w : null;
    var cs    = CONF_STYLES[conf] || CONF_STYLES.missing;
    var pct   = score != null ? Math.round(score * 100) : null;
    var contribStr = contribution != null ? '+' + (contribution * 100).toFixed(1) + '%' : '\u2014';
    var barColor = score != null ? SOURCE_COLORS[s] : '#2b3441';
    var glow = score != null ? '0 0 10px ' + SOURCE_COLORS[s] + '66' : 'none';

    rowsHtml +=
      '<div class="source-row">' +
        '<div class="source-name">' + SOURCE_LABELS[s] + '</div>' +
        '<div class="bar-track"><div class="bar-fill" style="width:' + (pct || 0) + '%;background:' + barColor + ';box-shadow:' + glow + '"></div></div>' +
        '<div class="bar-label">' + (pct != null ? pct + '% score' : 'no data') + '</div>' +
        '<div class="conf-badge" style="background:' + cs.bg + ';color:' + cs.color + '">' + conf + '</div>' +
        '<div class="contrib-cell">' + contribStr + ' pts</div>' +
      '</div>';
  });

  if (d.notes && d.notes.length) {
    function fmtNote(n) {
      return n
        .replace(/DepMap CERES/gi, '<span class="mono">DepMap CERES</span>')
        .replace(/Open Targets/gi, '<span class="mono">Open Targets</span>')
        .replace(/PHAROS/gi, '<span class="mono">PHAROS</span>')
        .replace(/Literature/gi, '<span class="mono">Literature</span>');
    }
    rowsHtml +=
      '<div style="margin-top:12px">' +
        '<div class="section-label">Audit trail</div>' +
        '<ul class="notes-list">' +
          d.notes.map(function(n) { return '<li>' + fmtNote(n) + '</li>'; }).join('') +
        '</ul>' +
      '</div>';
  }
  document.getElementById('source-rows').innerHTML = rowsHtml;

  buildWaterfall(d);
  buildRadar(d);
  scheduleNotifyHeight();
}

function buildWaterfall(d) {
  var active = SOURCE_ORDER.filter(function(s) { return d.source_scores[s] != null; });
  var labels = active.map(function(s) { return SOURCE_LABELS[s]; });
  var values = active.map(function(s) {
    return parseFloat((d.source_scores[s] * (d.weights_used[s] || 0) * 100).toFixed(1));
  });
  var colors = active.map(function(s) { return SOURCE_COLORS[s] + 'cc'; });

  if (waterfallChart) waterfallChart.destroy();
  waterfallChart = new Chart(document.getElementById('waterfall'), {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 5,
        borderSkipped: false,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: function(ctx) { return '+' + ctx.parsed.y.toFixed(1) + ' pts to final score'; }
          }
        }
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 11 }, color: '#a1a1aa' } },
        y: {
          min: 0, max: 35,
          title: { display: true, text: 'contribution (score points)', font: { size: 10 }, color: '#8b90a1' },
          ticks: { font: { size: 11 }, color: '#8b90a1', callback: function(v) { return v + '%'; } },
          grid: { color: '#1f242e' }
        }
      }
    }
  });
}

function buildRadar(d) {
  var labels = SOURCE_ORDER.map(function(s) { return SOURCE_LABELS[s]; });
  var scores = SOURCE_ORDER.map(function(s) {
    return d.source_scores[s] != null ? parseFloat((d.source_scores[s] * 100).toFixed(1)) : 0;
  });

  if (radarChart) radarChart.destroy();
  radarChart = new Chart(document.getElementById('radar'), {
    type: 'radar',
    data: {
      labels: labels,
      datasets: [{
        data: scores,
        backgroundColor: 'rgba(59,130,246,0.15)',
        borderColor: '#3B82F6',
        borderWidth: 1.5,
        pointBackgroundColor: SOURCE_ORDER.map(function(s) { return SOURCE_COLORS[s]; }),
        pointRadius: 4,
        pointHoverRadius: 6,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        r: {
          min: 0, max: 100,
          ticks: {
            stepSize: 25, font: { size: 10 }, color: '#8b90a1',
            backdropColor: 'transparent',
            callback: function(v) { return v + '%'; }
          },
          grid: { color: 'rgba(255,255,255,0.08)' },
          angleLines: { color: 'rgba(255,255,255,0.08)' },
          pointLabels: { font: { size: 11 }, color: '#cbd5f5' }
        }
      }
    }
  });
}

var tabsEl = document.getElementById('tabs');
var firstGene = null;

Object.keys(GENES).forEach(function(gene, i) {
  if (i === 0) firstGene = gene;
  var btn = document.createElement('button');
  btn.className = 'gene-tab' + (i === 0 ? ' active' : '');
  btn.textContent = gene;
  btn.onclick = function() {
    document.querySelectorAll('.gene-tab').forEach(function(t) { t.classList.remove('active'); });
    btn.classList.add('active');
    render(gene);
  };
  tabsEl.appendChild(btn);
});

if (firstGene) render(firstGene);
window.addEventListener("load", scheduleNotifyHeight);
window.addEventListener("resize", scheduleNotifyHeight);
</script>
</body>
</html>
"""


# ─── Converters ───────────────────────────────────────────────────────────────

_ALL_SOURCES = ['pharos', 'depmap', 'open_targets', 'literature']


def _scored_target_to_dict(st: ScoredTarget | dict | Any) -> dict:
    """Convert a ScoredTarget (Pydantic model or dict) into the JSON shape
    the HTML template expects."""
    if isinstance(st, ScoredTarget):
        d = st.model_dump()
    elif is_dataclass(st) and not isinstance(st, type):
        d = asdict(st)
    elif isinstance(st, dict):
        d = st
    else:
        raise TypeError(f"Expected ScoredTarget or dict, got {type(st)}")

    return {
        'target_score':        round(float(d.get('target_score', 0.0)), 4),
        'evidence_confidence': round(float(d.get('evidence_confidence', 0.0)), 4),
        'source_scores':       {s: d.get('source_scores', {}).get(s) for s in _ALL_SOURCES},
        'source_confidences':  {s: d.get('source_confidences', {}).get(s, 'missing') for s in _ALL_SOURCES},
        'weights_used':        {s: d.get('weights_used', {}).get(s, 0.0) for s in _ALL_SOURCES},
        'conflict_flag':       bool(d.get('conflict_flag', False)),
        'conflict_detail':     d.get('conflict_detail') or '',
        'missing_sources':     list(d.get('missing_sources', [])),
        'sparse_sources':      list(d.get('sparse_sources', [])),
        'notes':               list(d.get('notes', [])),
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def generate_evidence_html(
    scored_targets: ScoredTarget | dict | Any | list[ScoredTarget | dict | Any],
    output_path: Union[str, Path] = "artifacts/evidence_dashboard.html",
) -> Path:
    """
    Generate a self-contained HTML evidence dashboard.

    Parameters
    ----------
    scored_targets : ScoredTarget | dict | list[ScoredTarget | dict]
        One or more scored target objects.  Each must have a ``gene`` field.
    output_path : str | Path
        Where to write the HTML file.

    Returns
    -------
    Path   – the path of the written file.
    """
    if not isinstance(scored_targets, list):
        scored_targets = [scored_targets]

    if not scored_targets:
        raise ValueError("scored_targets must contain at least one result.")

    genes_dict: dict[str, dict] = {}
    for st in scored_targets:
        if isinstance(st, ScoredTarget):
            gene_name = st.gene
        elif is_dataclass(st):
            gene_name = getattr(st, "gene", "UNKNOWN")
        elif isinstance(st, dict):
            gene_name = st.get('gene', 'UNKNOWN')
        else:
            raise TypeError(f"Unsupported type: {type(st)}")
        genes_dict[gene_name] = _scored_target_to_dict(st)

    genes_json = json.dumps(genes_dict, indent=2)
    html = HTML_TEMPLATE.replace('__GENES_DATA__', genes_json)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding='utf-8')
    return out
