"""补货单行手改的验收测试：三页同数、超缺口整单回退、冻结行再开编辑拦下。

种子货道（点位 1）：
  A1 缺15  A2 满仓  B1 缺7  B2 满仓  C1 缺10  C2 超占
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.services.seed import seed_if_empty


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    db = session_local()
    seed_if_empty(db)
    db.close()

    def override_get_db():
        s = session_local()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    # 不用 with 包裹：跳过 lifespan，避免连接真实 Postgres
    yield TestClient(app)
    app.dependency_overrides.clear()


def run_order(client) -> dict:
    r = client.post("/api/refills/run?location_id=1")
    assert r.status_code == 200, r.text
    return r.json()


def line_of(order: dict, slot: str) -> dict:
    return next(l for l in order["lines"] if l["slot_no"] == slot)


def lane_id(client, slot: str) -> int:
    lanes = client.get("/api/lanes?location_id=1").json()
    return next(l["id"] for l in lanes if l["slot_no"] == slot)


def put_lines(client, order_id: int, **slot_qty):
    """slot_qty 形如 A1=12；返回响应对象。"""
    lanes = client.get("/api/lanes?location_id=1").json()
    by_slot = {l["slot_no"]: l["id"] for l in lanes}
    payload = {"lines": [{"lane_id": by_slot[s], "fill_qty": q} for s, q in slot_qty.items()]}
    return client.put(f"/api/refills/{order_id}/lines", json=payload)


def three_pages(client):
    latest = client.get("/api/refills/latest?location_id=1").json()
    summary = client.get("/api/refills/summary?location_id=1").json()
    full = client.get("/api/refills/full?location_id=1").json()
    return latest, summary, full


def assert_three_pages_consistent(latest, summary, full):
    line_sum = sum(l["fill_qty"] for l in latest["lines"])
    assert latest["total_fill"] == line_sum, "补货单合计必须等于各行补量之和"
    assert summary["total_fill"] == line_sum, "汇总必须与补货单同一套数"
    full_slots = {l["slot_no"] for l in full["lanes"]}
    expect_full = {l["slot_no"] for l in latest["lines"] if l["status"] == "full"}
    assert full_slots == expect_full, "满仓页必须与补货单状态同一套数"
    assert summary["full_count"] == len(expect_full)


def test_manual_edit_success_three_pages_consistent(client):
    order = run_order(client)
    a1 = line_of(order, "A1")
    assert (a1["gap"], a1["fill_qty"], a1["manual"]) == (15, 15, False)

    # 把 A1 改成小于缺口的正数
    r = put_lines(client, order["id"], A1=12)
    assert r.status_code == 200, r.text
    a1 = line_of(r.json(), "A1")
    assert (a1["fill_qty"], a1["manual"], a1["status"]) == (12, True, "need_fill")

    latest, summary, full = three_pages(client)
    assert line_of(latest, "A1")["fill_qty"] == 12
    assert summary["total_fill"] == 12 + 7 + 10 == 29
    assert summary["need_fill_count"] == 3
    assert summary["full_count"] == 2
    assert summary["overbooked_count"] == 1
    assert {l["slot_no"] for l in full["lanes"]} == {"A2", "B2"}
    assert_three_pages_consistent(latest, summary, full)


def test_edit_above_gap_fails_and_everything_reverts(client):
    order = run_order(client)
    assert put_lines(client, order["id"], A1=12).status_code == 200
    before = three_pages(client)

    # 再改成大于保存当下缺口（15）→ 整次失败
    r = put_lines(client, order["id"], A1=16)
    assert r.status_code == 400, r.text

    after = three_pages(client)
    assert before[0] == after[0], "补货单必须退回操作前"
    assert before[1] == after[1], "汇总必须退回操作前"
    assert before[2] == after[2], "满仓页必须退回操作前"
    assert line_of(after[0], "A1")["fill_qty"] == 12


def test_multi_line_save_is_atomic(client):
    order = run_order(client)
    # A1 合法、B1 超缺口：不许只改中 A1 的半截成功
    r = put_lines(client, order["id"], A1=5, B1=999)
    assert r.status_code == 400, r.text
    latest = client.get("/api/refills/latest?location_id=1").json()
    assert line_of(latest, "A1")["fill_qty"] == 15, "失败保存不得留下半截修改"
    assert line_of(latest, "B1")["fill_qty"] == 7
    assert latest["total_fill"] == 32


def test_frozen_over_gap_line_blocked_when_edit_reopened(client):
    order = run_order(client)
    assert put_lines(client, order["id"], A1=12).status_code == 200

    # 抬高 A1 库存：缺口 15 → 10，旧手改 12 冻结在单上且超出新缺口
    r = client.patch(f"/api/lanes/{lane_id(client, 'A1')}", json={"stock": 10})
    assert r.status_code == 200 and r.json()["gap"] == 10

    latest = client.get("/api/refills/latest?location_id=1").json()
    a1 = line_of(latest, "A1")
    assert a1["fill_qty"] == 12, "手改值冻结在单上，不得被回刷"
    assert a1["current_gap"] == 10
    assert a1["over_gap"] is True, "超缺口旧手改再次打开编辑必须被拦下"
    assert a1["status"] == "need_fill", "超缺口行不得被回写成满仓"

    # 拦下：只要超缺口行没修复，任何保存都整单失败
    assert put_lines(client, order["id"], B1=3).status_code == 400
    assert put_lines(client, order["id"], A1=12).status_code == 400
    # 修复到缺口以内后放行，三页重新同数
    r = put_lines(client, order["id"], A1=10)
    assert r.status_code == 200, r.text
    latest, summary, full = three_pages(client)
    assert summary["total_fill"] == 10 + 7 + 10 == 27
    assert_three_pages_consistent(latest, summary, full)


def test_void_order_forbidden_to_edit(client):
    order = run_order(client)
    r = client.post(f"/api/refills/{order['id']}/void")
    assert r.status_code == 200 and r.json()["status"] == "void"
    assert r.json()["editable"] is False
    r = put_lines(client, order["id"], A1=1)
    assert r.status_code == 409, "已作废单禁止手改"


def test_verified_order_forbidden_to_edit(client):
    order = run_order(client)
    r = client.post(f"/api/refills/{order['id']}/verify")
    assert r.status_code == 200 and r.json()["status"] == "verified"
    r = put_lines(client, order["id"], A1=1)
    assert r.status_code == 409, "已核销单禁止手改"


def test_earlier_history_orders_not_refreshed(client):
    o1 = run_order(client)
    assert put_lines(client, o1["id"], A1=12).status_code == 200
    o2 = run_order(client)
    assert o2["id"] != o1["id"]
    assert put_lines(client, o2["id"], A1=4).status_code == 200

    h1 = client.get(f"/api/refills/{o1['id']}").json()
    assert line_of(h1, "A1")["fill_qty"] == 12, "更早的历史单不得被这次手改回刷"
    assert h1["total_fill"] == 29
    # 三页跟随最新单（o2: A1=4 → 4+7+10=21）
    latest, summary, full = three_pages(client)
    assert latest["id"] == o2["id"] and summary["total_fill"] == 21
    assert_three_pages_consistent(latest, summary, full)


def test_manual_edit_does_not_touch_lane_stock(client):
    before = {l["slot_no"]: (l["stock"], l["in_transit"])
              for l in client.get("/api/lanes?location_id=1").json()}
    order = run_order(client)
    assert put_lines(client, order["id"], A1=12, B1=3, C1=0).status_code == 200
    after = {l["slot_no"]: (l["stock"], l["in_transit"])
             for l in client.get("/api/lanes?location_id=1").json()}
    assert before == after, "货道库存与在途不得因手改而变"


def test_edit_to_zero_moves_line_to_full_page(client):
    order = run_order(client)
    r = put_lines(client, order["id"], A1=0)
    assert r.status_code == 200, r.text
    assert line_of(r.json(), "A1")["status"] == "full", "手改为 0 即不再待补"

    latest, summary, full = three_pages(client)
    assert "A1" in {l["slot_no"] for l in full["lanes"]}, "满仓页按手改后是否仍待补刷新"
    assert summary["total_fill"] == 7 + 10 == 17
    assert summary["need_fill_count"] == 2
    assert summary["full_count"] == 3
    assert_three_pages_consistent(latest, summary, full)


def test_negative_fill_rejected(client):
    order = run_order(client)
    r = put_lines(client, order["id"], A1=-1)
    assert r.status_code == 422, "手改量不得为负"


def test_edit_unknown_lane_and_missing_order(client):
    order = run_order(client)
    r = client.put(f"/api/refills/{order['id']}/lines",
                   json={"lines": [{"lane_id": 9999, "fill_qty": 1}]})
    assert r.status_code == 400
    r = client.put("/api/refills/9999/lines", json={"lines": []})
    assert r.status_code == 404
