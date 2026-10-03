"""Vending refill: gap = capacity - stock - in_transit; fills capped by gap; no negative fills.

手改规则：
- 补货单行可手改补量，合法区间为 0 <= qty <= 保存当下缺口；
- 校验是「整单」的：任一行非法则整次保存作废，调用方必须整体回滚；
- 手改成功后 fill_qty 冻结在单据上，不随后续缺口变化而变；
- 小票 / 汇总 / 满仓三页只能由 merge_order_view 这一份视图派生，保证同数。
"""
from __future__ import annotations
from dataclasses import asdict, dataclass

STATUS_ACTIVE = "active"
STATUS_VOID = "void"
STATUS_RECONCILED = "reconciled"
EDITABLE_STATUSES = (STATUS_ACTIVE,)


class ManualFillError(ValueError):
    """行手改非法：message 直接可回显给前端。整次保存必须视为失败。"""


@dataclass
class FillLine:
    lane_id: int
    slot_no: str
    sku_name: str
    capacity: int
    stock: int
    in_transit: int
    gap: int
    fill_qty: int
    status: str  # need_fill | full | overbooked
    manual: bool = False


def compute_gap(capacity: int, stock: int, in_transit: int) -> int:
    return capacity - stock - in_transit


def build_fill_lines(lanes: list[dict], requested: dict[int, int] | None = None) -> list[FillLine]:
    """requested optional desired fill per lane_id; capped by gap; never negative."""
    lines: list[FillLine] = []
    for lane in lanes:
        gap = compute_gap(int(lane["capacity"]), int(lane["stock"]), int(lane["in_transit"]))
        if gap < 0:
            status = "overbooked"
            fill = 0
        elif gap == 0:
            status = "full"
            fill = 0
        else:
            status = "need_fill"
            desire = gap if requested is None else int(requested.get(lane["id"], gap))
            fill = max(0, min(desire, gap))
        lines.append(FillLine(
            lane_id=lane["id"], slot_no=lane["slot_no"], sku_name=lane["sku_name"],
            capacity=lane["capacity"], stock=lane["stock"], in_transit=lane["in_transit"],
            gap=gap, fill_qty=fill, status=status,
        ))
    return lines


def summarize(lines: list[FillLine]) -> dict:
    return {
        "total_fill": sum(l.fill_qty for l in lines),
        "need_fill_count": sum(1 for l in lines if l.status == "need_fill"),
        "full_count": sum(1 for l in lines if l.status == "full"),
        "overbooked_count": sum(1 for l in lines if l.status == "overbooked"),
        "lines": [asdict(l) for l in lines],
    }


def current_lane_map(lanes: list[dict]) -> dict[int, dict]:
    """货道当下数据（容量/库存/在途/缺口），按 lane_id 索引。"""
    return {
        int(l["id"]): {
            "capacity": int(l["capacity"]),
            "stock": int(l["stock"]),
            "in_transit": int(l["in_transit"]),
            "gap": compute_gap(int(l["capacity"]), int(l["stock"]), int(l["in_transit"])),
        }
        for l in lanes
    }


def current_gap_map(lanes: list[dict]) -> dict[int, int]:
    """货道当下缺口，按 lane_id 索引。"""
    return {lid: l["gap"] for lid, l in current_lane_map(lanes).items()}


def merge_order_view(stored_lines: list[dict], lanes_now: dict[int, dict] | list[dict]) -> list[dict]:
    """单据冻结行 + 货道当下数据 -> 唯一一份三页同源视图。

    - fill_qty 永远取单据上冻结的手改/生成值，库存与在途变化不改它；
    - 容量/库存/在途/缺口取货道当下值（货道删除时退回单据快照）；
    - status 只按「当下缺口 + 冻结补量」判定（满仓页按新手改后是否仍待补刷新：
      fill_qty == 0 且当下缺口为 0 即 full；fill_qty == 0 且当下缺口为负即 overbooked；
      其余（含 fill_qty > 0）一律 need_fill）；
    - 不做任何清洗：冻结补量超过当下缺口时照常带出，交由编辑入口拦截，
      绝不静默回写成满仓。
    """
    if isinstance(lanes_now, list):
        lanes_now = current_lane_map(lanes_now)
    view: list[dict] = []
    for line in stored_lines:
        lane_id = int(line["lane_id"])
        now = lanes_now.get(lane_id)
        if now is not None:
            capacity, stock, in_transit, gap_now = (
                now["capacity"], now["stock"], now["in_transit"], now["gap"])
        else:
            capacity, stock, in_transit = (
                int(line.get("capacity", 0)), int(line.get("stock", 0)),
                int(line.get("in_transit", 0)))
            gap_now = int(line.get("gap", 0))
        fill_qty = int(line["fill_qty"])
        manual = bool(line.get("manual", False))
        if gap_now < 0:
            status = "overbooked" if fill_qty == 0 else "need_fill"
        elif gap_now == 0:
            status = "full" if fill_qty == 0 else "need_fill"
        else:
            status = "need_fill"
        view.append({
            **line,
            "capacity": capacity,
            "stock": stock,
            "in_transit": in_transit,
            "gap": gap_now,
            "fill_qty": fill_qty,
            "status": status,
            "manual": manual,
            "stale": manual and fill_qty > gap_now,
        })
    return view


def summarize_view(view_lines: list[dict]) -> dict:
    """对 merge_order_view 的结果做合计——小票、汇总、满仓的唯一汇总口径。"""
    return {
        "total_fill": sum(int(l["fill_qty"]) for l in view_lines),
        "need_fill_count": sum(1 for l in view_lines if l["status"] == "need_fill"),
        "full_count": sum(1 for l in view_lines if l["status"] == "full"),
        "overbooked_count": sum(1 for l in view_lines if l["status"] == "overbooked"),
        "lines": view_lines,
    }


def stale_manual_lines(stored_lines: list[dict], gaps_now: dict[int, int]) -> list[dict]:
    """已手改冻结、但补量已超过当下缺口的行——重新打开编辑必须拦下。"""
    return [
        l for l in stored_lines
        if bool(l.get("manual", False)) and int(l["fill_qty"]) > int(gaps_now.get(int(l["lane_id"]), 0))
    ]


def apply_manual_fills(stored_lines: list[dict], gaps_now: dict[int, int],
                       requested: dict[int, int]) -> list[dict]:
    """整单校验后按行落新手改量。

    任一行非法（非整数、小于 0、超过该行保存当下缺口）即抛 ManualFillError，
    不返回任何半成品；调用方必须整单回滚，不许出现只改中某几行的状态。
    成功返回可直接写回 lines_json 的新行列表（fill_qty 冻结、manual 置位）。
    货道库存与在途不在此函数职责内，绝不修改。
    """
    new_lines = [dict(l) for l in stored_lines]
    by_lane = {int(l["lane_id"]): l for l in new_lines}

    # 先全量校验，全部通过才动手——保证原子性。
    parsed: dict[int, int] = {}
    for lane_id, raw in requested.items():
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ManualFillError(f"货道 {lane_id}：补量必须是整数")
        qty = int(raw)
        if qty < 0:
            raise ManualFillError(f"货道 {lane_id}：补量不能为负（收到 {qty}）")
        gap_now = int(gaps_now.get(int(lane_id))) if int(lane_id) in gaps_now else None
        if gap_now is None:
            raise ManualFillError(f"货道 {lane_id}：不属于本机货道，禁止改量")
        if qty > gap_now:
            raise ManualFillError(
                f"货道 {lane_id}：补量 {qty} 超出当下缺口 {gap_now}，整次保存失败")
        parsed[int(lane_id)] = qty

    for lane_id, qty in parsed.items():
        line = by_lane[lane_id]
        line["fill_qty"] = qty
        line["manual"] = True
    return new_lines
