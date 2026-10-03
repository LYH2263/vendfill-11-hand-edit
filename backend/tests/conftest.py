import os

# 必须在 import app.config 之前指定测试库
os.environ["DATABASE_URL"] = "sqlite:///./pytest_manual.db"
os.environ["SEED_ON_EMPTY"] = "false"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with TestClient(app) as c:
        yield c
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def seed(client) -> None:
    """复刻 services.seed 的种子数据（A1: 20 容量 / 5 库存 / 0 在途，缺口 15）。"""
    from app.services.seed import seed_if_empty
    s = SessionLocal()
    try:
        seed_if_empty(s)
    finally:
        s.close()


def lane_by_slot(client, slot: str) -> dict:
    return next(l for l in client.get("/api/lanes").json() if l["slot_no"] == slot)
