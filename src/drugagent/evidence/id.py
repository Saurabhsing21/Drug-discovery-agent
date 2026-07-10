from __future__ import annotations

import hashlib
import json
from typing import Any


def evidence_id_base(*, source: str, target_id: str, disease_id: str | None, evidence_type: str) -> str:
    disease_part = (disease_id or "NA").strip() or "NA"
    return f"{source}:{target_id}:{disease_part}:{evidence_type}"


def compute_evidence_id(
    *,
    source: str,
    target_id: str,
    disease_id: str | None,
    evidence_type: str,
    raw_value: Any,
    support: Any,
) -> str:
    """Compute a deterministic evidence id for traceability across runs.

    Contract:
      base = "{source}:{target_id}:{disease_id_or_NA}:{evidence_type}"
      fingerprint = json.dumps({"base": base, "raw_value": raw_value, "support": support}, sort_keys=True, default=str)
      evidence_id = base + ":" + sha1(fingerprint)[:10]
    """
    base = evidence_id_base(
        source=str(source),
        target_id=str(target_id),
        disease_id=disease_id,
        evidence_type=str(evidence_type),
    )
    fingerprint = json.dumps(
        {"base": base, "raw_value": raw_value, "support": support},
        sort_keys=True,
        default=str,
    )
    digest = hashlib.sha1(fingerprint.encode("utf-8")).hexdigest()[:10]  # noqa: S324 (non-cryptographic id)
    return f"{base}:{digest}"

