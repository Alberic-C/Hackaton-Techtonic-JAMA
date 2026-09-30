from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

_settings = get_settings()

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False},  # FastAPI exécute les endpoints sync dans un threadpool
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")  # lecteurs non bloqués par l'ingestion n8n
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    from .models import Base

    Base.metadata.create_all(engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
