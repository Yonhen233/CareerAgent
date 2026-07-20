import os

os.environ["EMBEDDING_PROVIDER"] = "hash"
os.environ["RERANKER_ENABLED"] = "false"
os.environ["LLM_FALLBACK_ENABLED"] = "true"
os.environ["LANGGRAPH_CHECKPOINT_FILE"] = ".tmp_test/pytest_langgraph_checkpoints.sqlite"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models import entities  # noqa: F401


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
