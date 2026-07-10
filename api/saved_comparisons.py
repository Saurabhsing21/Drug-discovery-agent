from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from api.db import get_db
from api.db_models import SavedComparison

router = APIRouter(prefix="/saved-comparisons", tags=["saved_comparisons"])

class SavedComparisonBase(BaseModel):
    title: str
    run_a_id: str
    run_b_id: str
    compare_markdown: Optional[str] = None
    data_snapshot: Optional[dict] = None

class SavedComparisonResponse(SavedComparisonBase):
    id: str
    created_at: datetime

    class Config:
        from_attributes = True

@router.get("/", response_model=List[SavedComparisonResponse])
def list_comparisons(db: Session = Depends(get_db)):
    comparisons = db.query(SavedComparison).order_by(SavedComparison.created_at.desc()).all()
    # convert UUID to str
    res = []
    for comp in comparisons:
        res.append(SavedComparisonResponse(
            id=str(comp.id),
            title=comp.title,
            run_a_id=comp.run_a_id,
            run_b_id=comp.run_b_id,
            compare_markdown=comp.compare_markdown,
            data_snapshot=comp.data_snapshot,
            created_at=comp.created_at
        ))
    return res

@router.post("/", response_model=SavedComparisonResponse)
def save_comparison(data: SavedComparisonBase, db: Session = Depends(get_db)):
    new_comp = SavedComparison(
        title=data.title,
        run_a_id=data.run_a_id,
        run_b_id=data.run_b_id,
        compare_markdown=data.compare_markdown,
        data_snapshot=data.data_snapshot
    )
    db.add(new_comp)
    db.commit()
    db.refresh(new_comp)
    return SavedComparisonResponse(
        id=str(new_comp.id),
        title=new_comp.title,
        run_a_id=new_comp.run_a_id,
        run_b_id=new_comp.run_b_id,
        compare_markdown=new_comp.compare_markdown,
        data_snapshot=new_comp.data_snapshot,
        created_at=new_comp.created_at
    )

@router.get("/{comp_id}", response_model=SavedComparisonResponse)
def get_comparison(comp_id: str, db: Session = Depends(get_db)):
    comp = db.query(SavedComparison).filter(SavedComparison.id == comp_id).first()
    if not comp:
        raise HTTPException(status_code=404, detail="Comparison not found")
    return SavedComparisonResponse(
        id=str(comp.id),
        title=comp.title,
        run_a_id=comp.run_a_id,
        run_b_id=comp.run_b_id,
        compare_markdown=comp.compare_markdown,
        data_snapshot=comp.data_snapshot,
        created_at=comp.created_at
    )
