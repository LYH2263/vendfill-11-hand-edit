"""验收场景：种子 -> A1 手改小于缺口（三页同数）-> 改大于缺口失败且回退
-> 抬高 A1 库存使缺口变小 -> 再打开编辑，超缺口旧手改被拦。"""
from tests.conftest import lane_by_slot, seed


def _line(order, slot):
    return next(l for l in order["lines"] if l["slot_no"] == slot)


def test_seed_then_manual_three_pages_agree(client):
    seed(client)
    run = client.post("/api/refills/run?location_id=1").json()
    oid = run["id"]
    a1 = _line(run, "A1")
    assert a1["gap"] == 15 and a1["fill_qty"] == 15

    # edit 入口可打开
    assert client.get(f"/api/refills/{oid}/edit").status_code == 200

    # A1 手改为小于缺口的正数 8
    r = client.patch(f"/api/refills/{oid}/lines", json={"lines": [{"lane_id": a1["lane_id"], "fill_qty": 8}]})
    assert r.status_code == 200, r.text
    order = r.json()
    assert _line(order, "A1")["fill_qty"] == 8
    assert _line(order, "A1")["manual"] is True
    # 汇总 = 各行新手改量之和
    assert order["total_fill"] == sum(l["fill_qty"] for l in order["lines"])

    # 三页同数
    got = client.get(f"/api/refills/{oid}").json()
    summ = client.get("/api/refills/summary?location_id=1").json()
    full = client.get("/api/refills/full?location_id=1").json()
    assert summ["total_fill"] == got["total_fill"]
    assert summ["need_fill_count"] == got["need_fill_count"]
    assert summ["full_count"] == got["full_count"]
    assert summ["order_id"] == oid and full["order_id"] == oid
    full_ids = {l["lane_id"] for l in full["lanes"]}
    assert full_ids == {l["lane_id"] for l in got["lines"] if l["status"] == "full"}
    assert _line(got, "A1")["lane_id"] not in full_ids  # 手改 8 仍待补


def test_over_gap_save_fails_and_whole_order_rolls_back(client):
    seed(client)
    oid = client.post("/api/refills/run?location_id=1").json()["id"]
    before = client.get(f"/api/refills/{oid}").json()

    # A1 缺口 15：提交 99 -> 整次失败
    a1_id = _line(before, "A1")["lane_id"]
    r = client.patch(f"/api/refills/{oid}/lines", json={"lines": [{"lane_id": a1_id, "fill_qty": 99}]})
    assert r.status_code == 409

    # 该单所有行、汇总合计一律退回操作前
    after = client.get(f"/api/refills/{oid}").json()
    assert [(l["lane_id"], l["fill_qty"], l.get("manual")) for l in after["lines"]] == \
           [(l["lane_id"], l["fill_qty"], l.get("manual")) for l in before["lines"]]
    assert after["total_fill"] == before["total_fill"]
    summ = client.get("/api/refills/summary?location_id=1").json()
    assert summ["total_fill"] == before["total_fill"]
    # edit 仍可打开（没有半截手改落地）
    assert client.get(f"/api/refills/{oid}/edit").status_code == 200


def test_multi_line_atomic_no_half_success(client):
    seed(client)
    oid = client.post("/api/refills/run?location_id=1").json()["id"]
    before = client.get(f"/api/refills/{oid}").json()
    a1, b1 = _line(before, "A1"), _line(before, "B1")
    # 同一批里一行合法、一行超缺口 -> 合法那行也不许改中
    r = client.patch(f"/api/refills/{oid}/lines", json={"lines": [
        {"lane_id": a1["lane_id"], "fill_qty": 2},
        {"lane_id": b1["lane_id"], "fill_qty": 999},
    ]})
    assert r.status_code == 409
    after = client.get(f"/api/refills/{oid}").json()
    assert _line(after, "A1")["fill_qty"] == _line(before, "A1")["fill_qty"]
    assert _line(after, "B1")["fill_qty"] == _line(before, "B1")["fill_qty"]
    assert not any(l.get("manual") for l in after["lines"])


def test_gap_shrinks_then_edit_blocked_but_manual_value_frozen(client):
    seed(client)
    run = client.post("/api/refills/run?location_id=1").json()
    oid = run["id"]
    a1_id = _line(run, "A1")["lane_id"]
    # 先手改 8（缺口 15，合法）
    assert client.patch(f"/api/refills/{oid}/lines",
                        json={"lines": [{"lane_id": a1_id, "fill_qty": 8}]}).status_code == 200

    # 抬高 A1 库存 5 -> 18，缺口缩到 2；库存与在途不回刷补量
    r = client.patch(f"/api/lanes/{a1_id}", json={"stock": 18})
    assert r.status_code == 200

    view = client.get(f"/api/refills/{oid}").json()
    a1 = _line(view, "A1")
    assert a1["gap"] == 2 and a1["fill_qty"] == 8 and a1["stale"] is True
    assert a1["stock"] == 18
    # 再次打开编辑必须拦下
    ed = client.get(f"/api/refills/{oid}/edit")
    assert ed.status_code == 409
    # 再 PATCH 同样拦下，且不把超缺口行回写成满仓
    assert client.patch(f"/api/refills/{oid}/lines",
                        json={"lines": [{"lane_id": a1_id, "fill_qty": 1}]}).status_code == 409
    still = client.get(f"/api/refills/{oid}").json()
    assert _line(still, "A1")["fill_qty"] == 8
    assert _line(still, "A1")["status"] == "need_fill"
    # 满仓页不含 A1（冻结 8 > 0）
    full_ids = {l["lane_id"] for l in client.get("/api/refills/full?location_id=1").json()["lanes"]}
    assert a1_id not in full_ids


def test_stock_and_in_transit_unchanged_by_manual_edit(client):
    seed(client)
    run = client.post("/api/refills/run?location_id=1").json()
    a1 = _line(run, "A1")
    client.patch(f"/api/refills/{run['id']}/lines",
                 json={"lines": [{"lane_id": a1["lane_id"], "fill_qty": 8}]})
    lane = lane_by_slot(client, "A1")
    assert lane["stock"] == 5 and lane["in_transit"] == 0


def test_void_and_reconciled_orders_not_editable(client):
    seed(client)
    oid = client.post("/api/refills/run?location_id=1").json()["id"]
    a1_id = _line(client.get(f"/api/refills/{oid}").json(), "A1")["lane_id"]
    assert client.post(f"/api/refills/{oid}/void").status_code == 200
    assert client.get(f"/api/refills/{oid}/edit").status_code == 409
    assert client.patch(f"/api/refills/{oid}/lines",
                        json={"lines": [{"lane_id": a1_id, "fill_qty": 1}]}).status_code == 409

    oid2 = client.post("/api/refills/run?location_id=1").json()["id"]
    a1b = _line(client.get(f"/api/refills/{oid2}").json(), "A1")["lane_id"]
    client.post(f"/api/refills/{oid2}/reconcile")
    assert client.get(f"/api/refills/{oid2}/edit").status_code == 409
    assert client.patch(f"/api/refills/{oid2}/lines",
                        json={"lines": [{"lane_id": a1b, "fill_qty": 1}]}).status_code == 409


def test_manual_edit_does_not_rewrite_earlier_history_order(client):
    seed(client)
    first = client.post("/api/refills/run?location_id=1").json()
    first_id = first["id"]
    first_before = client.get(f"/api/refills/{first_id}").json()
    # 再生成一张新单并手改，更早的历史单不得被回刷
    second = client.post("/api/refills/run?location_id=1").json()
    a1_id = _line(second, "A1")["lane_id"]
    client.patch(f"/api/refills/{second['id']}/lines",
                 json={"lines": [{"lane_id": a1_id, "fill_qty": 3}]})
    first_after = client.get(f"/api/refills/{first_id}").json()
    assert [(l["lane_id"], l["fill_qty"]) for l in first_after["lines"]] == \
           [(l["lane_id"], l["fill_qty"]) for l in first_before["lines"]]
    assert first_after["total_fill"] == first_before["total_fill"]
