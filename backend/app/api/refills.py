import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Lane, Location, RefillOrder
from app.services.fill_engine import (
    EDITABLE_STATUSES,
    STATUS_RECONCILED,
    STATUS_VOID,
    ManualFillError,
    apply_manual_fills,
    build_fill_lines,
    current_lane_map,
    merge_order_view,
    stale_manual_lines,
    summarize,
    summarize_view,
)

router = APIRouter(prefix="/refills", tags=["refills"])

STATUS_LABEL = {STATUS_VOID: "已作废", STATUS_RECONCILED: "已核销"}


def _lanes_payload(db: Session, location_id: int) -> list[dict]:
    lanes = db.scalars(
        select(Lane).where(Lane.location_id == location_id).order_by(Lane.slot_no)
    ).all()
    return [{"id": l.id, "slot_no": l.slot_no, "sku_name": l.sku_name,
             "capacity": l.capacity, "stock": l.stock, "in_transit": l.in_transit}
            for l in lanes]


def _get_order_or_404(db: Session, order_id: int) -> RefillOrder:
    order = db.get(RefillOrder, order_id)
    if not order:
        raise HTTPException(404, "补货单不存在")
    return order


def _order_view(db: Session, order: RefillOrder) -> dict:
    """小票 / 汇总 / 满仓三页共用的唯一取数口径。"""
    stored = json.loads(order.lines_json)
    lines = stored.get("lines", stored) if isinstance(stored, dict) else stored
    lanes_now = current_lane_map(_lanes_payload(db, order.location_id))
    view = merge_order_view(lines, lanes_now)
    return {"id": order.id, "location_id": order.location_id,
            "status": order.status, **summarize_view(view)}


def _guard_editable(db: Session, order: RefillOrder) -> tuple[list[dict], dict[int, dict]]:
    """打开编辑与保存共用的服务端闸门：状态 + 超缺口冻结行。

    已作废/已核销单禁止手改；已手改行补量超过当下缺口时一律拦下，
    不返回可编辑正数、也不把该行洗成满仓。
    """
    if order.status not in EDITABLE_STATUSES:
        raise HTTPException(409, f"该补货单{STATUS_LABEL.get(order.status, order.status)}，禁止手改")
    data = json.loads(order.lines_json)
    stored = data.get("lines", data) if isinstance(data, dict) else data
    lanes_now = current_lane_map(_lanes_payload(db, order.location_id))
    gaps = {lid: l["gap"] for lid, l in lanes_now.items()}
    stale = stale_manual_lines(stored, gaps)
    if stale:
        desc = "、".join(f"{l.get('slot_no', l['lane_id'])}（已冻 {l['fill_qty']}，现缺口 "
                        f"{gaps.get(int(l['lane_id']), 0)}）" for l in stale)
        raise HTTPException(409, f"手改行补量已超过当下缺口，禁止继续编辑：{desc}")
    return stored, lanes_now


@router.post("/run")
def run_refill(location_id: int = 1, db: Session = Depends(get_db)):
    loc = db.get(Location, location_id)
    if not loc:
        raise HTTPException(404, "点位不存在")
    summary = summarize(build_fill_lines(_lanes_payload(db, location_id)))
    order = RefillOrder(location_id=location_id, created_at=datetime.utcnow(),
                        status="active", lines_json=json.dumps(summary, ensure_ascii=False))
    db.add(order)
    db.commit()
    db.refresh(order)
    return {"id": order.id, "location_id": location_id, "status": order.status, **summary}


@router.get("/latest")
def latest(location_id: int = 1, db: Session = Depends(get_db)):
    order = db.scalars(select(RefillOrder).where(RefillOrder.location_id == location_id)
                       .order_by(RefillOrder.id.desc())).first()
    if not order:
        return run_refill(location_id=location_id, db=db)
    return _order_view(db, order)


@router.get("/full")
def full_lanes(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {"location_id": location_id, "order_id": data["id"], "status": data["status"],
            "lanes": [l for l in data["lines"] if l["status"] == "full"]}


@router.get("/summary")
def refill_summary(location_id: int = 1, db: Session = Depends(get_db)):
    data = latest(location_id=location_id, db=db)
    return {
        "location_id": location_id,
        "order_id": data["id"],
        "status": data["status"],
        "total_fill": data["total_fill"],
        "need_fill_count": data["need_fill_count"],
        "full_count": data["full_count"],
        "overbooked_count": data["overbooked_count"],
    }


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db)):
    return _order_view(db, _get_order_or_404(db, order_id))


@router.get("/{order_id}/edit")
def open_edit(order_id: int, db: Session = Depends(get_db)):
    """打开行编辑器：废单/核销单、超缺口冻结行都在这里被拦下。"""
    order = _get_order_or_404(db, order_id)
    _guard_editable(db, order)
    return _order_view(db, order)


class LineFill(BaseModel):
    lane_id: int
    fill_qty: int = Field(..., ge=0)


class ManualPatch(BaseModel):
    lines: list[LineFill]


@router.patch("/{order_id}/lines")
def patch_lines(order_id: int, payload: ManualPatch, db: Session = Depends(get_db)):
    """按行手改补量。

    整单校验：任一行 0 > qty 或 qty > 保存当下缺口，则整次保存失败——
    显式回滚，该单所有行、汇总合计、满仓列表一律保持操作前，不允许半截成功。
    货道库存与在途不被触碰。
    """
    order = _get_order_or_404(db, order_id)
    stored, lanes_now = _guard_editable(db, order)
    gaps = {lid: l["gap"] for lid, l in lanes_now.items()}

    requested = {l.lane_id: l.fill_qty for l in payload.lines}
    try:
        new_lines = apply_manual_fills(stored, gaps, requested)
    except ManualFillError as exc:
        db.rollback()  # 整次保存失败，任何状态都不落库
        raise HTTPException(409, str(exc))

    # 合计以新手改量重算，写回的仍是「同一套数」的唯一来源 lines_json。
    new_view = merge_order_view(new_lines, lanes_now)
    order.lines_json = json.dumps(summarize_view(new_view), ensure_ascii=False)
    db.commit()
    db.refresh(order)
    return _order_view(db, order)


def _set_status(order_id: int, status: str, db: Session) -> dict:
    order = _get_order_or_404(db, order_id)
    order.status = status
    db.commit()
    db.refresh(order)
    return _order_view(db, order)


@router.post("/{order_id}/void")
def void_order(order_id: int, db: Session = Depends(get_db)):
    return _set_status(order_id, STATUS_VOID, db)


@router.post("/{order_id}/reconcile")
def reconcile_order(order_id: int, db: Session = Depends(get_db)):
    return _set_status(order_id, STATUS_RECONCILED, db)
