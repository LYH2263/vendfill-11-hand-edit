from dataclasses import asdict

from app.services.fill_engine import (
    ManualFillError,
    apply_manual_fills,
    build_fill_lines,
    current_gap_map,
    merge_order_view,
    stale_manual_lines,
    summarize_view,
)


def _lane(lid, slot, cap, stock, transit):
    return {"id": lid, "slot_no": slot, "sku_name": "水", "capacity": cap,
            "stock": stock, "in_transit": transit}


def test_manual_freeze_keeps_fill_when_gap_shrinks():
    # 生成时缺口 15、补量 15，手改为 8 冻结；之后缺口缩到 2
    lanes = [_lane(1, "A1", 20, 5, 0)]
    stored = [asdict(l) for l in build_fill_lines(lanes)]
    assert stored[0]["gap"] == 15 and stored[0]["fill_qty"] == 15
    stored = apply_manual_fills(stored, current_gap_map(lanes), {1: 8})
    assert stored[0]["fill_qty"] == 8 and stored[0]["manual"] is True

    lanes_now = [_lane(1, "A1", 20, 18, 0)]  # gap 掉到 2
    view = merge_order_view(stored, lanes_now)
    assert view[0]["fill_qty"] == 8          # 冻结量不随缺口变
    assert view[0]["gap"] == 2
    assert view[0]["status"] == "need_fill"  # 绝不洗成满仓
    assert view[0]["stale"] is True
    assert stale_manual_lines(stored, current_gap_map(lanes_now))[0]["lane_id"] == 1


def test_zero_fill_with_gap_stays_need_fill_then_becomes_full():
    # 手改为 0 且仍有缺口 -> 待补；缺口归零后 -> 满仓页刷新出来
    stored = [{"lane_id": 1, "slot_no": "A1", "sku_name": "水", "capacity": 20,
               "stock": 5, "in_transit": 0, "gap": 15, "fill_qty": 0,
               "status": "need_fill", "manual": True}]
    view = merge_order_view(stored, [_lane(1, "A1", 20, 5, 0)])
    assert view[0]["status"] == "need_fill"
    view2 = merge_order_view(stored, [_lane(1, "A1", 20, 20, 0)])
    assert view2[0]["status"] == "full" and view2[0]["fill_qty"] == 0


def test_apply_over_gap_fails_atomically_and_leaves_input_untouched():
    stored = [
        {"lane_id": 1, "slot_no": "A1", "fill_qty": 15, "manual": False},
        {"lane_id": 2, "slot_no": "B1", "fill_qty": 7, "manual": False},
    ]
    gaps = {1: 15, 2: 7}
    snapshot = [dict(l) for l in stored]
    # 第二行超缺口：整批失败，第一行也不许被改中
    try:
        apply_manual_fills(stored, gaps, {1: 3, 2: 99})
        assert False, "应当抛出 ManualFillError"
    except ManualFillError:
        pass
    assert stored == snapshot


def test_apply_negative_and_unknown_lane_rejected():
    import pytest
    stored = [{"lane_id": 1, "slot_no": "A1", "fill_qty": 5, "manual": False}]
    with pytest.raises(ManualFillError):
        apply_manual_fills(stored, {1: 10}, {1: -1})
    with pytest.raises(ManualFillError):
        apply_manual_fills(stored, {1: 10}, {9: 1})


def test_summary_total_is_sum_of_manual_fills():
    stored = [
        {"lane_id": i, "slot_no": f"A{i}", "fill_qty": q, "manual": True}
        for i, q in enumerate((8, 0, 3), start=1)
    ]
    lanes = [_lane(1, "A1", 20, 12, 0), _lane(2, "A2", 10, 10, 0),
             _lane(3, "A3", 10, 7, 0)]
    s = summarize_view(merge_order_view(stored, lanes))
    assert s["total_fill"] == 11 == sum(l["fill_qty"] for l in s["lines"])
