"""Vending refill: gap = capacity - stock - in_transit; fills capped by gap; no negative fills."""
from __future__ import annotations
from dataclasses import asdict, dataclass

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
    manual: bool = False  # True once the fill qty was hand-edited on the order

def compute_gap(capacity: int, stock: int, in_transit: int) -> int:
    return capacity - stock - in_transit

def derive_status(gap: int, fill_qty: int) -> str:
    """Line status from the order's snapshot gap and its (possibly hand-edited) fill.

    gap < 0 stays overbooked; a positive fill means the lane is still to be
    refilled; anything with zero to refill counts as full on the full-lanes page.
    """
    if gap < 0:
        return "overbooked"
    return "need_fill" if fill_qty > 0 else "full"

def validate_fills(lines: list[dict], current_gaps: dict[int, int]) -> list[dict]:
    """Check every line's fill_qty against the lane's gap *right now*.

    Returns one entry per violating line (empty list = all good). A fill is
    valid only inside [0, max(0, current_gap)]; callers must treat any
    violation as fatal for the whole save (no partial writes).
    """
    violations: list[dict] = []
    for line in lines:
        lane_id = int(line["lane_id"])
        gap_now = int(current_gaps.get(lane_id, line.get("gap", 0)))
        cap = max(0, gap_now)
        fill = int(line["fill_qty"])
        if fill < 0 or fill > cap:
            violations.append({
                "lane_id": lane_id,
                "slot_no": line.get("slot_no", ""),
                "fill_qty": fill,
                "max_allowed": cap,
            })
    return violations

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
