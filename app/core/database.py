from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False, "timeout": 30}
    return {}


settings = get_settings()
engine = create_engine(
    settings.database_url,
    connect_args=_connect_args(settings.database_url),
    future=True,
)


if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


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
        for column_name in ["prompt_tokens", "completion_tokens", "total_tokens"]:
            if column_name not in llm_log_columns:
                statements.append(
                    f"ALTER TABLE llm_call_logs ADD COLUMN {column_name} INTEGER NOT NULL DEFAULT 0"
                )
    for table_name in ["profiles", "jobs", "agent_runs"]:
        if table_name in tables:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "tenant_id" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN tenant_id VARCHAR(128)")
            if table_name == "agent_runs" and "user_id" not in columns:
                statements.append("ALTER TABLE agent_runs ADD COLUMN user_id VARCHAR(128)")
    if "app_users" in tables:
        app_user_columns = {column["name"] for column in inspector.get_columns("app_users")}
        if "password_hash" not in app_user_columns:
            statements.append("ALTER TABLE app_users ADD COLUMN password_hash VARCHAR(512)")
    for table_name in ["match_results", "resume_versions", "applications", "interview_preps"]:
        if table_name in tables:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "idempotency_key" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN idempotency_key VARCHAR(255)")
            if table_name == "match_results" and "retrieval_quality_json" not in columns:
                statements.append(
                    "ALTER TABLE match_results ADD COLUMN retrieval_quality_json JSON NOT NULL DEFAULT '{}'"
                )
    for table_name in ["resume_versions", "interview_preps"]:
        if table_name in tables:
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "lifecycle_status" not in columns:
                statements.append(
                    f"ALTER TABLE {table_name} ADD COLUMN lifecycle_status VARCHAR(32) NOT NULL DEFAULT 'active'"
                )
            if "withdrawn_at" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN withdrawn_at DATETIME")
            if "withdrawal_reason" not in columns:
                statements.append(f"ALTER TABLE {table_name} ADD COLUMN withdrawal_reason TEXT")
    if "applications" in tables:
        application_columns = {column["name"] for column in inspector.get_columns("applications")}
        if "withdrawn_at" not in application_columns:
            statements.append("ALTER TABLE applications ADD COLUMN withdrawn_at DATETIME")
        if "withdrawal_reason" not in application_columns:
            statements.append("ALTER TABLE applications ADD COLUMN withdrawal_reason TEXT")
    if "job_search_sessions" in tables:
        job_search_columns = {column["name"] for column in inspector.get_columns("job_search_sessions")}
        if "retrieval_quality_json" not in job_search_columns:
            statements.append(
                "ALTER TABLE job_search_sessions ADD COLUMN retrieval_quality_json JSON NOT NULL DEFAULT '{}'"
            )

    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        if "llm_call_logs" in tables:
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_llm_call_logs_created_at "
                    "ON llm_call_logs(created_at)"
                )
            )
        for table_name in ["match_results", "resume_versions", "applications", "interview_preps"]:
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
