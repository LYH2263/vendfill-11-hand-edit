from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane
from app.services.fill_engine import compute_gap
router = APIRouter(prefix="/lanes", tags=["lanes"])


@router.get("")
def list_lanes(location_id: int | None = None, db: Session = Depends(get_db)):
    q = select(Lane).order_by(Lane.slot_no)
    if location_id is not None:
        q = q.where(Lane.location_id == location_id)
    out = []
    for r in db.scalars(q).all():
        gap = compute_gap(r.capacity, r.stock, r.in_transit)
        out.append({"id": r.id, "location_id": r.location_id, "slot_no": r.slot_no, "sku_name": r.sku_name,
                    "capacity": r.capacity, "stock": r.stock, "in_transit": r.in_transit, "gap": gap,
                    "fill_pct": round(r.stock / r.capacity * 100, 1) if r.capacity else 0})
    return out


class LaneAdjust(BaseModel):
    """货道实物盘点调整：只动库存/在途本身，绝不回刷任何已生成补货单。"""
    stock: int | None = Field(default=None, ge=0)
    in_transit: int | None = Field(default=None, ge=0)


@router.patch("/{lane_id}")
def adjust_lane(lane_id: int, payload: LaneAdjust, db: Session = Depends(get_db)):
    lane = db.get(Lane, lane_id)
    if not lane:
        raise HTTPException(404, "货道不存在")
    if payload.stock is not None:
        lane.stock = payload.stock
    if payload.in_transit is not None:
        lane.in_transit = payload.in_transit
    db.commit()
    db.refresh(lane)
    gap = compute_gap(lane.capacity, lane.stock, lane.in_transit)
    return {"id": lane.id, "slot_no": lane.slot_no, "sku_name": lane.sku_name,
            "capacity": lane.capacity, "stock": lane.stock, "in_transit": lane.in_transit,
            "gap": gap,
            "fill_pct": round(lane.stock / lane.capacity * 100, 1) if lane.capacity else 0}
