from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlalchemy import inspect, text

from app.api.router import api_router
from app.config import settings
from app.database import Base, SessionLocal, engine
from app.services.seed import seed_if_empty


def _ensure_schema() -> None:
    """轻量迁移：旧库 refill_orders 补 status 列（create_all 不会改已有表）。"""
    inspector = inspect(engine)
    if "refill_orders" in inspector.get_table_names():
        cols = {c["name"] for c in inspector.get_columns("refill_orders")}
        if "status" not in cols:
            with engine.begin() as conn:
                conn.execute(text(
                    "ALTER TABLE refill_orders ADD COLUMN status VARCHAR(16) "
                    "NOT NULL DEFAULT 'active'"))


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    if settings.seed_on_empty:
        db = SessionLocal()
        try:
            seed_if_empty(db)
        finally:
            db.close()
    yield


app = FastAPI(title="VendFill", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")
