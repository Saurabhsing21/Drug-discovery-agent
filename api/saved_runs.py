from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from api.db import new_session
from api.db_models import SavedRun


class SavedRunsUnavailable(Exception):
    pass


def _require_session():
    session = new_session()
    if session is None:
        raise SavedRunsUnavailable("DATABASE_URL is not configured")
    return session


def list_saved_runs() -> list[dict[str, Any]]:
    session = _require_session()
    try:
        rows = session.execute(select(SavedRun).order_by(SavedRun.created_at.desc())).scalars().all()
        return [
            {
                "id": str(r.id),
                "run_id": r.run_id,
                "title": r.title,
                "gene_symbol": r.gene_symbol,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
    finally:
        session.close()


def get_saved_run(saved_id: str) -> dict[str, Any] | None:
    session = _require_session()
    try:
        rid = uuid.UUID(saved_id)
        row = session.get(SavedRun, rid)
        if row is None:
            return None
        return {
            "id": str(row.id),
            "run_id": row.run_id,
            "title": row.title,
            "gene_symbol": row.gene_symbol,
            "disease_id": row.disease_id,
            "objective": row.objective,
            "summary_markdown": row.summary_markdown,
            "scored_target": row.scored_target,
            "final_dossier": row.final_dossier,
            "evidence_graph": row.evidence_graph,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat(),
        }
    finally:
        session.close()


def rename_saved_run(saved_id: str, title: str) -> dict[str, Any] | None:
    session = _require_session()
    try:
        rid = uuid.UUID(saved_id)
        row = session.get(SavedRun, rid)
        if row is None:
            return None
        row.title = title
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "id": str(row.id),
            "run_id": row.run_id,
            "title": row.title,
            "gene_symbol": row.gene_symbol,
            "created_at": row.created_at.isoformat(),
        }
    finally:
        session.close()


def delete_saved_run(saved_id: str) -> bool:
    session = _require_session()
    try:
        rid = uuid.UUID(saved_id)
        row = session.get(SavedRun, rid)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True
    finally:
        session.close()


def upsert_saved_run_from_snapshot(payload: dict[str, Any]) -> str:
    session = _require_session()
    try:
        run_id = str(payload["run_id"])
        existing = session.execute(select(SavedRun).where(SavedRun.run_id == run_id)).scalar_one_or_none()
        if existing is None:
            row = SavedRun(**payload)
            session.add(row)
            session.commit()
            session.refresh(row)
            return str(row.id)

        for key, value in payload.items():
            setattr(existing, key, value)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return str(existing.id)
    finally:
        session.close()
