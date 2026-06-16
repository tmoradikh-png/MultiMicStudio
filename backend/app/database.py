"""Database engine, session factory, and Base."""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

# SQLite needs check_same_thread=False for use with FastAPI's threadpool.
connect_args = (
    {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. MVP uses create_all; production should use Alembic migrations."""
    from app import models  # noqa: F401  (register models)

    Base.metadata.create_all(bind=engine)
    _ensure_columns()


def _ensure_columns() -> None:
    """Lightweight dev migration: add columns introduced after a DB already exists.

    create_all never ALTERs existing tables, so a dev SQLite file from an earlier
    version would be missing newer columns. Add them in place (idempotent).
    Production should use Alembic instead.
    """
    from sqlalchemy import inspect, text

    wanted: dict[str, dict[str, str]] = {
        "sessions": {"current_take_id": "VARCHAR(36)"},
        "recordings": {"take_id": "VARCHAR(36)"},
        "session_participants": {"guest_token": "VARCHAR(64)"},
        "processed_projects": {
            "final_audio_stereo_url": "VARCHAR(1024)",
            "final_audio_enhanced_url": "VARCHAR(1024)",
            "enhancement_mode": "VARCHAR(40)",
        },
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in wanted.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in present:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
