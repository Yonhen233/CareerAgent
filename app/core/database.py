from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args=_connect_args(settings.database_url),
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    statements: list[str] = []
    if "resume_chunks" in tables:
        resume_chunk_columns = {column["name"] for column in inspector.get_columns("resume_chunks")}
        if "metadata_json" not in resume_chunk_columns:
            statements.append("ALTER TABLE resume_chunks ADD COLUMN metadata_json JSON NOT NULL DEFAULT '{}'")
    if "llm_call_logs" in tables:
        llm_log_columns = {column["name"] for column in inspector.get_columns("llm_call_logs")}
        if "context_json" not in llm_log_columns:
            statements.append("ALTER TABLE llm_call_logs ADD COLUMN context_json JSON NOT NULL DEFAULT '{}'")
    for table_name in ["profiles", "jobs", "agent_runs"]:
        if table_name in tables:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "tenant_id" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN tenant_id VARCHAR(128)")
    if "app_users" in tables:
        app_user_columns = {column["name"] for column in inspector.get_columns("app_users")}
        if "password_hash" not in app_user_columns:
            statements.append("ALTER TABLE app_users ADD COLUMN password_hash VARCHAR(512)")
    for table_name in ["resume_versions", "applications", "interview_preps"]:
        if table_name in tables:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "idempotency_key" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN idempotency_key VARCHAR(255)")

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        for table_name in ["resume_versions", "applications", "interview_preps"]:
            if table_name in tables:
                index_name = f"ix_{table_name}_idempotency_key"
                indexes = {index["name"] for index in inspector.get_indexes(table_name)}
                if index_name not in indexes:
                    conn.execute(text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table_name}(idempotency_key)"))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
