from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane
from app.services.fill_engine import compute_gap
router = APIRouter(prefix="/lanes", tags=["lanes"])

class LaneStockUpdate(BaseModel):
    stock: int | None = Field(default=None, ge=0)
    in_transit: int | None = Field(default=None, ge=0)

def _lane_row(r: Lane) -> dict:
    gap = compute_gap(r.capacity, r.stock, r.in_transit)
    return {"id": r.id, "location_id": r.location_id, "slot_no": r.slot_no, "sku_name": r.sku_name,
            "capacity": r.capacity, "stock": r.stock, "in_transit": r.in_transit, "gap": gap,
            "fill_pct": round(r.stock / r.capacity * 100, 1) if r.capacity else 0}

@router.get("")
def list_lanes(location_id: int | None = None, db: Session = Depends(get_db)):
    q = select(Lane).order_by(Lane.slot_no)
    if location_id is not None: q = q.where(Lane.location_id == location_id)
    return [_lane_row(r) for r in db.scalars(q).all()]

@router.patch("/{lane_id}")
def update_lane_stock(lane_id: int, payload: LaneStockUpdate, db: Session = Depends(get_db)):
    """调整货道库存/在途（如到货入仓）。缺口随之变化，但已生成补货单保持冻结。"""
    lane = db.get(Lane, lane_id)
    if not lane: raise HTTPException(404, "货道不存在")
    if payload.stock is not None: lane.stock = payload.stock
    if payload.in_transit is not None: lane.in_transit = payload.in_transit
    db.commit(); db.refresh(lane)
    return _lane_row(lane)
