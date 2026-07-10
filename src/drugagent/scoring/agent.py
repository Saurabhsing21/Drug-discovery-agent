from __future__ import annotations

import math
from typing import Any, Optional
from drugagent.scoring.schemas import SourceEvidence, ScoredTarget
from drugagent.scoring.depmap_norm import (
    normalize_depmap_ceres,
    DEPMAP_CLIP_MIN as DEPMAP_CLIP_MIN_CONST,
    DEPMAP_CLIP_MAX as DEPMAP_CLIP_MAX_CONST,
)

class NormalizationScoringAgent:
    """
    Implements normalization, confidence scoring, dynamic weight rebalancing, 
    and conflict detection across 4 therapeutic evidence sources.
    """

    # Base weights as per design doc
    BASE_WEIGHTS = {
        'pharos': 0.30,
        'depmap': 0.30,
        'open_targets': 0.25,
        'literature': 0.15
    }

    # Normalization constants
    TDL_SCORES = {
        'Tdark': 0.10,
        'Tbio': 0.35,
        'Tchem': 0.70,
        'Tclin': 1.00,
    }
    DEPMAP_CLIP_MIN = DEPMAP_CLIP_MIN_CONST
    DEPMAP_CLIP_MAX = DEPMAP_CLIP_MAX_CONST

    SOURCE_ALIASES = {
        # Project-wide SourceName uses "opentargets"; scoring design uses "open_targets".
        "opentargets": "open_targets",
        "open targets": "open_targets",
        "open-targets": "open_targets",
        "ext_opentargets": "open_targets",
        "ext_pharos": "pharos",
    }

    def _canonical_source(self, source: str) -> str:
        key = str(source or "").strip().lower()
        return self.SOURCE_ALIASES.get(key, key)

    def _coerce_tdl(self, value: Any) -> str | None:
        if value is None:
            return None
        reverse_tdl = {0: 'Tdark', 1: 'Tbio', 2: 'Tchem', 3: 'Tclin'}

        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return reverse_tdl.get(int(value))
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            # Accept either canonical strings ("Tclin") or numeric encodings ("3").
            if text.isdigit():
                return reverse_tdl.get(int(text))
            lower = text.lower().replace(" ", "")
            mapping = {"tdark": "Tdark", "tbio": "Tbio", "tchem": "Tchem", "tclin": "Tclin"}
            return mapping.get(lower)
        return None

    def _normalize_pharos(self, evidence: SourceEvidence) -> Optional[float]:
        if not evidence.data_present:
            return None
        # Use TDL from metadata if available, else use raw_signal.
        tdl_raw = evidence.metadata.get('tdl', None)
        if tdl_raw is None:
            tdl_raw = evidence.raw_signal

        tdl = self._coerce_tdl(tdl_raw) or "Tdark"
        return self.TDL_SCORES.get(tdl, 0.10)

    def _normalize_depmap(self, evidence: SourceEvidence) -> Optional[float]:
        if not evidence.data_present or evidence.raw_signal is None:
            return None
        if not isinstance(evidence.raw_signal, (int, float)):
            return None
        normalized = normalize_depmap_ceres(evidence.raw_signal)
        if normalized is None:
            return None
        return round(normalized, 4)

    def _normalize_open_targets(self, evidence: SourceEvidence) -> Optional[float]:
        if not evidence.data_present or evidence.raw_signal is None:
            return None

        requested = evidence.metadata.get("requested_disease_id")
        disease_scores = evidence.metadata.get("disease_scores", [])
        if requested and isinstance(disease_scores, list):
            for entry in disease_scores:
                if not isinstance(entry, dict):
                    continue
                disease_id = entry.get("disease_id")
                score_value = entry.get("score")
                if disease_id and score_value is not None and str(disease_id).lower() == str(requested).lower():
                    score = score_value
                    evidence.metadata["score_source_disease"] = disease_id
                    break
            else:
                score = None
        else:
            score = None

        if score is None:
            all_scores = evidence.metadata.get('all_disease_scores', [])
            if all_scores:
                score = max(all_scores)
                if isinstance(disease_scores, list):
                    idx = all_scores.index(score)
                    if 0 <= idx < len(disease_scores):
                        entry = disease_scores[idx]
                        if isinstance(entry, dict) and entry.get("disease_id"):
                            evidence.metadata["score_source_disease"] = entry.get("disease_id")
            else:
                score = evidence.raw_signal
            
        return max(0.0, min(1.0, float(score)))

    def _normalize_literature(self, evidence: SourceEvidence) -> Optional[float]:
        if not evidence.data_present:
            # Note: doc says if zero papers, return 0.0. 
            # But if source is missing entirely, return None.
            return None
        
        count = evidence.raw_signal if isinstance(evidence.raw_signal, (int, float)) else evidence.total_available
        if count == 0:
            return 0.0
        
        # Formula: min(log10(count + 1) / 3.0, 1.0)
        normalized = min(math.log10(count + 1) / 3.0, 1.0)
        return round(normalized, 4)

    def compute_source_confidence(self, source: str, evidence: SourceEvidence) -> str:
        source = self._canonical_source(source)
        if not evidence.data_present:
            return 'missing'
        
        count = evidence.total_available
        
        if source == 'pharos':
            return 'high'  # Always high if present as per doc
        
        if source == 'depmap':
            if count >= 20:
                return 'high'
            if count >= 5:
                return 'medium'
            return 'low'
            
        if source == 'open_targets':
            # Special case: "definitive zero" (e.g., query succeeded but no associations).
            if count == 0 and evidence.raw_signal is not None:
                return 'high'
            if count >= 5:
                return 'high'
            if count >= 2:
                return 'medium'
            return 'low'
            
        if source == 'literature':
            if count >= 100:
                return 'high'
            if count >= 10:
                return 'medium'
            return 'low'
            
        return 'low'

    def score(self, evidence_list: list[SourceEvidence]) -> ScoredTarget:
        if not evidence_list:
            # Minimal fallback for totally empty input
            return ScoredTarget(
                gene="Unknown",
                target_score=0.0,
                evidence_confidence=0.0,
                source_scores={},
                source_confidences={},
                weights_used={}
            )

        gene = evidence_list[0].gene
        source_ev_map = {self._canonical_source(ev.source): ev for ev in evidence_list}
        
        # 1. Normalization
        source_scores = {}
        normalizers = {
            'pharos': self._normalize_pharos,
            'depmap': self._normalize_depmap,
            'open_targets': self._normalize_open_targets,
            'literature': self._normalize_literature
        }
        
        for src, normalize_fn in normalizers.items():
            ev = source_ev_map.get(src)
            if ev:
                score = normalize_fn(ev)
                if score is not None:
                    source_scores[src] = score

        # 2. Confidence
        source_confidences = {}
        for src in self.BASE_WEIGHTS.keys():
            ev = source_ev_map.get(src)
            if ev:
                source_confidences[src] = self.compute_source_confidence(src, ev)
            else:
                source_confidences[src] = 'missing'

        # 3. Dynamic Weight Rebalancing
        missing_sources = []
        unscorable_sources = []
        for src in self.BASE_WEIGHTS.keys():
            ev = source_ev_map.get(src)
            if not ev or not ev.data_present:
                missing_sources.append(src)
            elif src not in source_scores:
                unscorable_sources.append(src)

        present_sources = [src for src in self.BASE_WEIGHTS.keys() if src in source_scores]
        
        total_present_base_weight = sum(self.BASE_WEIGHTS[src] for src in present_sources)
        
        weights_used = {src: 0.0 for src in self.BASE_WEIGHTS.keys()}
        if total_present_base_weight > 0:
            for src in present_sources:
                weights_used[src] = self.BASE_WEIGHTS[src] / total_present_base_weight

        # 4. Target Score
        target_score = sum(source_scores[src] * weights_used[src] for src in present_sources)
        
        # 5. Overall Evidence Confidence
        # high=1.0, medium=0.6, low=0.3, missing=0.0
        conf_map = {'high': 1.0, 'medium': 0.6, 'low': 0.3, 'missing': 0.0}
        
        # Doc says: "weighted average of individual source confidences using the FINAL rebalanced weights"
        # However, rebalanced weights are only for present sources. 
        # For overall confidence, we should probably factor in missing sources as well.
        # Let's use the BASE weights for overall confidence to penalize missing sources.
        overall_confidence = sum(conf_map[source_confidences[src]] * self.BASE_WEIGHTS[src] for src in self.BASE_WEIGHTS.keys())

        # 6. Conflict Detection
        dep_ev = source_ev_map.get("depmap")
        dep_ceres = None
        if dep_ev and isinstance(dep_ev.raw_signal, (int, float)):
            dep_ceres = float(dep_ev.raw_signal)
        conflict_flag, conflict_detail = self._detect_conflict(source_scores, dep_ceres)

        # 7. Sparse Sources
        sparse_sources = []
        for src, conf in source_confidences.items():
            if conf == 'low':
                sparse_sources.append(src)

        notes = []
        if missing_sources:
            notes.append(f"Sources with no data: {missing_sources}. Weights rebalanced.")
        if unscorable_sources:
            notes.append(f"Sources had data but were unscorable (missing raw_signal/fields): {unscorable_sources}.")
        if sparse_sources:
            notes.append(f"Sources with sparse data: {sparse_sources}.")
        if len(present_sources) == 1:
            notes.append("Score based entirely on single source — treat with caution.")
        if conflict_flag:
            notes.append("Inter-source conflict detected — see detail for stratification needs.")
        # DepMap note: positive CERES (or non-negative) indicates non-essentiality and will clamp to 0.
        dep_ev = source_ev_map.get("depmap")
        if dep_ev and dep_ev.data_present and isinstance(dep_ev.raw_signal, (int, float)) and float(dep_ev.raw_signal) > 0.0:
            notes.append("DepMap CERES is positive (>0): clamped dependency contribution to 0.0 (non-essential).")

        return ScoredTarget(
            gene=gene,
            target_score=round(target_score, 4),
            evidence_confidence=round(overall_confidence, 4),
            source_scores=source_scores,
            source_confidences=source_confidences,
            weights_used=weights_used,
            conflict_flag=conflict_flag,
            conflict_detail=conflict_detail,
            missing_sources=missing_sources,
            sparse_sources=sparse_sources,
            notes=notes
        )

    def _detect_conflict(self, source_scores: dict[str, float], dep_ceres: float | None) -> tuple[bool, Optional[str]]:
        threshold = 0.50
        dep_positive = dep_ceres is not None and dep_ceres > 0.0
        pairs = [
            (
                "pharos",
                "depmap",
                (
                    "DepMap shows non-essentiality (positive CERES) — gene may be tractable but not required for cancer cell survival."
                    if dep_positive
                    else "High tractability but low genetic essentiality — may be relevant in different tissue or disease context than DepMap cell lines tested."
                ),
            ),
            (
                "pharos",
                "open_targets",
                "Drug exists or chemical tools available, but weak disease association — drug may target a different disease than the one being analyzed here.",
            ),
            (
                "depmap",
                "open_targets",
                (
                    "DepMap shows non-essentiality (positive CERES) — weak disease association further reduces confidence in target relevance."
                    if dep_positive
                    else "Strong cancer cell dependency but weak disease association score — may indicate context-specific essentiality not captured by OT GWAS data."
                ),
            ),
        ]
        conflicts: list[str] = []
        for src_a, src_b, detail in pairs:
            score_a = source_scores.get(src_a)
            score_b = source_scores.get(src_b)
            if score_a is None or score_b is None:
                continue
            if abs(score_a - score_b) >= threshold:
                conflicts.append(
                    f"{src_a.upper()} ({score_a:.2f}) vs {src_b.upper()} ({score_b:.2f}): {detail}"
                )
        if conflicts:
            return True, " | ".join(conflicts)
        return False, None
