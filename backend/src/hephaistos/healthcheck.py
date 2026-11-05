"""Read-only container health checks; never create a schema or import CUDA."""

from __future__ import annotations

import argparse
import json
import socket
import sqlite3
from datetime import UTC, datetime
from urllib.request import urlopen

from hephaistos.config import ConfigError, Settings, load_settings
from hephaistos.storage.local import LocalStorage, UnsafePath


def check_api() -> bool:
    try:
        with urlopen("http://127.0.0.1:8000/api/v1/system/health", timeout=2) as response:
            body = response.read(4097)
            return bool(response.status == 200 and len(body) <= 4096 and json.loads(body) == {"status": "ok"})
    except (OSError, ValueError):
        return False


def fresh(value: str, *, now: datetime, max_age: int) -> bool:
    try:
        date = datetime.fromisoformat(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=UTC)  # SQLite stores UTC without tzinfo.
        age = (now - date).total_seconds()
        return 0 <= age < max_age
    except (ValueError, TypeError):
        return False


def check_worker(settings: Settings, *, now: datetime | None = None) -> bool:
    try:
        storage = LocalStorage(settings.hephaistos_data_dir)
        path = storage._check(storage.root / "db" / "hephaistos.sqlite3")
        if not path.is_file():
            return False
        prefix = socket.gethostname() + ":"
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = connection.execute(
                "SELECT state, last_heartbeat_at FROM worker WHERE substr(id,1,?)=? "
                "ORDER BY last_heartbeat_at DESC LIMIT 1",
                (len(prefix), prefix),
            ).fetchone()
            return bool(
                row
                and row[0] not in {"DEGRADED", "STOPPING"}
                and fresh(row[1], now=now or datetime.now(UTC), max_age=settings.job_lease_seconds)
            )
        finally:
            connection.close()
    except (OSError, sqlite3.Error, UnsafePath):
        return False


def check_janitor(settings: Settings, *, now: datetime | None = None) -> bool:
    try:
        storage = LocalStorage(settings.hephaistos_data_dir)
        path = storage._check(storage.root / "janitor-status.json")
        with path.open() as handle:
            body = json.loads(handle.read(4097))
        return bool(
            isinstance(body, dict)
            and body.get("ok") is True
            and isinstance(body.get("at"), str)
            and fresh(body["at"], now=now or datetime.now(UTC), max_age=settings.job_lease_seconds)
        )
    except (OSError, ValueError, UnsafePath):
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Check one local service without GPU access")
    parser.add_argument("service", choices=("api", "worker", "janitor"))
    args = parser.parse_args()
    try:
        healthy = (
            check_api()
            if args.service == "api"
            else (check_worker(load_settings()) if args.service == "worker" else check_janitor(load_settings()))
        )
    except ConfigError:
        healthy = False
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
