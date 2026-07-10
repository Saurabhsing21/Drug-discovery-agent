from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func, JSON, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SavedRun(Base):
    __tablename__ = "saved_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)

    gene_symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    disease_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    objective: Mapped[str | None] = mapped_column(String(512), nullable=True)

    summary_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_target: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    final_dossier: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_graph: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class SavedComparison(Base):
    __tablename__ = "saved_comparisons"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_a_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_b_id: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    compare_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    data_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
