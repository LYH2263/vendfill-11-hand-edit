import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import (
    build_fill_lines,
    compute_gap,
    derive_status,
    summarize,
    validate_fills,
)

router = APIRouter(prefix="/refills", tags=["refills"])

ORDER_OPEN = "open"        # 已生成、未作废 —— 允许手改
ORDER_VOID = "void"        # 已作废 —— 禁止手改
ORDER_VERIFIED = "verified"  # 已核销 —— 禁止手改


class LineEdit(BaseModel):
    lane_id: int
    fill_qty: int = Field(ge=0)  # 手改量不得为负；上限在保存当下按缺口校验


class LinesEditPayload(BaseModel):
    lines: list[LineEdit]


def _current_gaps(db: Session, location_id: int) -> dict[int, int]:
    """Live gap per lane — the “保存当下缺口”, never written back into orders."""
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id)).all()
    return {l.id: compute_gap(l.capacity, l.stock, l.in_transit) for l in lanes}


def _order_payload(order: RefillOrder, db: Session) -> dict:
    """Stored (frozen) order lines annotated with live gap context for the UI."""
    data = json.loads(order.lines_json)
    gaps = _current_gaps(db, order.location_id)
    lines = []
    for l in data["lines"]:
        cur = gaps.get(l["lane_id"], l["gap"])
        lines.append({
            **l,
            "manual": bool(l.get("manual", False)),
            "current_gap": cur,
            # 冻结行补量超过保存当下缺口：打开编辑时必须拦下
            "over_gap": int(l["fill_qty"]) > max(0, cur),
        })
    return {
        "id": order.id,
        "location_id": order.location_id,
        "status": order.status,
        "editable": order.status == ORDER_OPEN,
        "created_at": order.created_at.isoformat() if order.created_at else None,
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
        "lines": lines,
    }


def _get_order(db: Session, order_id: int) -> RefillOrder:
    order = db.get(RefillOrder, order_id)
    if not order:
        raise HTTPException(404, "补货单不存在")
    return order


@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if not loc: raise HTTPException(404, "点位不存在")
    lanes = db.scalars(select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)).all()
    payload = [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
                "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit} for l in lanes]
    summary = summarize(build_fill_lines(payload))
    order = RefillOrder(location_id=location_id, created_at=datetime.utcnow(),
                        status=ORDER_OPEN, lines_json=json.dumps(summary, ensure_ascii=False))
    db.add(order); db.commit(); db.refresh(order)
    return _order_payload(order, db)

@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    order = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                       .order_by(RefillOrder.id.desc())).first()
    if not order:
        return run_refill(location_id=location_id, db=db)
    return _order_payload(order, db)

@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {"location_id": location_id, "lanes": [l for l in data["lines"] if l["status"] == "full"]}

@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {
        "location_id": location_id,
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
    }

@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    return _order_payload(_get_order(db, order_id), db)

@router.put("/{order_id}/lines")
def edit_lines(order_id: int, payload: LinesEditPayload, db: Session = Depends(get_db)):
    """按行手改补量。整次保存原子生效：任一行超出保存当下缺口，整单回退，不写半截。"""
    order = _get_order(db, order_id)
    if order.status != ORDER_OPEN:
        raise HTTPException(409, "已作废或已核销的补货单禁止手改")
    data = json.loads(order.lines_json)
    stored = {int(l["lane_id"]): dict(l) for l in data["lines"]}
    edits: dict[int, int] = {}
    for item in payload.lines:
        if item.lane_id not in stored:
            raise HTTPException(400, f"货道 {item.lane_id} 不在补货单 #{order_id} 中")
        edits[item.lane_id] = item.fill_qty

    # 合并本单所有行（手改 + 未动）后，逐行对照保存当下缺口校验。
    # 已冻结但已超过当前缺口的旧手改行同样拦下：不修复就不许再保存。
    gaps = _current_gaps(db, order.location_id)
    merged = []
    for lane_id, line in stored.items():
        new_fill = edits.get(lane_id, int(line["fill_qty"]))
        merged.append({**line, "fill_qty": new_fill})
    violations = validate_fills(merged, gaps)
    if violations:
        raise HTTPException(400, {
            "msg": "存在超出保存当下缺口的补量，整次保存已取消，单据未改动",
            "violations": violations,
        })

    # 校验全部通过才落库：只动本单 lines_json，货道库存/在途与历史单一律不碰。
    for line in merged:
        line["manual"] = bool(line.get("manual", False)) or line["lane_id"] in edits
        line["status"] = derive_status(int(line["gap"]), int(line["fill_qty"]))
    data["lines"] = merged
    data["total_fill"] = sum(int(l["fill_qty"]) for l in merged)
    data["need_fill_count"] = sum(1 for l in merged if l["status"] == "need_fill")
    data["full_count"] = sum(1 for l in merged if l["status"] == "full")
    data["overbooked_count"] = sum(1 for l in merged if l["status"] == "overbooked")
    order.lines_json = json.dumps(data, ensure_ascii=False)
    db.commit(); db.refresh(order)
    return _order_payload(order, db)

@router.post("/{order_id}/void")
def void_order(order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if order.status == ORDER_VERIFIED:
        raise HTTPException(409, "已核销的补货单不能作废")
    if order.status != ORDER_VOID:
        order.status = ORDER_VOID
        db.commit(); db.refresh(order)
    return _order_payload(order, db)

@router.post("/{order_id}/verify")
def verify_order(order_id: int, db: Session = Depends(get_db)):
    order = _get_order(db, order_id)
    if order.status == ORDER_VOID:
        raise HTTPException(409, "已作废的补货单不能核销")
    if order.status != ORDER_VERIFIED:
        order.status = ORDER_VERIFIED
        db.commit(); db.refresh(order)
    return _order_payload(order, db)
