from app.services.fill_engine import (
    build_fill_lines,
    compute_gap,
    derive_status,
    summarize,
    validate_fills,
)

def test_gap_basic():
    assert compute_gap(20, 5, 0) == 15
    assert compute_gap(20, 10, 5) == 5

def test_no_negative_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 12, "in_transit": 0}]
    lines = build_fill_lines(lanes)
    assert lines[0].fill_qty == 0
    assert lines[0].status == "overbooked"

def test_cap_by_gap():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 20, "stock": 5, "in_transit": 0}]
    lines = build_fill_lines(lanes, requested={1: 100})
    assert lines[0].fill_qty == 15
    assert lines[0].gap == 15

def test_full_zero_fill():
    lanes = [{"id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 10, "stock": 8, "in_transit": 2}]
    s = summarize(build_fill_lines(lanes))
    assert s["full_count"] == 1
    assert s["total_fill"] == 0

def test_derive_status():
    assert derive_status(-2, 0) == "overbooked"
    assert derive_status(15, 12) == "need_fill"
    assert derive_status(15, 0) == "full"   # 手改为 0 → 不再待补
    assert derive_status(0, 0) == "full"

def test_validate_fills_against_current_gap():
    lines = [
        {"lane_id": 1, "slot_no": "A1", "gap": 15, "fill_qty": 12},
        {"lane_id": 2, "slot_no": "A2", "gap": 0, "fill_qty": 0},
    ]
    assert validate_fills(lines, {1: 15, 2: 0}) == []
    bad = validate_fills(lines, {1: 10, 2: 0})  # 缺口缩小，冻结手改 12 超缺口
    assert bad == [{"lane_id": 1, "slot_no": "A1", "fill_qty": 12, "max_allowed": 10}]
    bad = validate_fills(lines, {1: -3, 2: 0})  # 超占道只允许 0
    assert bad[0]["max_allowed"] == 0
