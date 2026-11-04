"""SQLite engine shared by API, workers, janitor and migrations."""

from pathlib import Path
from sqlite3 import Connection

from sqlalchemy import URL, Engine, create_engine, event


def database_url(data_dir: Path) -> URL:
    return URL.create("sqlite", database=str(data_dir.absolute() / "db" / "hephaistos.sqlite3"))


def configure_sqlite(engine: Engine, *, busy_timeout_ms: int = 5000) -> None:
    if engine.dialect.name != "sqlite":
        return
    if busy_timeout_ms < 0:
        raise ValueError("busy_timeout_ms must be nonnegative")

    @event.listens_for(engine, "connect")
    def configure(connection: Connection, _record: object) -> None:
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute("PRAGMA journal_mode=WAL")
        finally:
            cursor.close()


def create_database_engine(data_dir: Path, *, busy_timeout_ms: int = 5000) -> Engine:
    """Prepare the database directory, never create tables or run migrations."""
    (data_dir / "db").mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url(data_dir))
    configure_sqlite(engine, busy_timeout_ms=busy_timeout_ms)
    return engine
