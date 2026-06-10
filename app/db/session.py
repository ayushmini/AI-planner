from pathlib import Path
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from app.core.config import DATABASE_URL

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
if DATABASE_URL.startswith("sqlite:///"):
    sqlite_path = Path(DATABASE_URL.replace("sqlite:///", ""))
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)


def _run_migrations() -> None:
    """Safely add new columns to existing tables without losing data.
    SQLite's ALTER TABLE ADD COLUMN is idempotent — if the column already
    exists the exception is silently swallowed."""
    from sqlalchemy import text

    migrations = [
        ("memory", "chat_session_id", "TEXT"),
        ("agentsession", "title", "TEXT NOT NULL DEFAULT ''"),
        ("agentsession", "calendar_snapshot", "TEXT NOT NULL DEFAULT ''"),
        ("agentsession", "last_accessed_at", "DATETIME"),
        ("agentsession", "finished_at", "DATETIME"),
    ]
    with engine.connect() as conn:
        for table, col, definition in migrations:
            try:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {definition}"))
                conn.commit()
            except Exception:
                # Column already exists — safe to ignore
                pass


def create_db_and_tables() -> None:
    import app.db.models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _run_migrations()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session

