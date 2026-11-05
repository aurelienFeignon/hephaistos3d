"""Frequent recovery ticks, configurable maintenance, and an on-disk health heartbeat."""

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from threading import Event

import structlog
from sqlalchemy.exc import SQLAlchemyError

from hephaistos.janitor.service import CycleReport, Janitor
from hephaistos.queue.broker import utc
from hephaistos.storage.atomic import write_atomically

LOG = structlog.get_logger()


def heartbeat(janitor: Janitor, report: CycleReport) -> None:
    path = janitor.storage._check(janitor.storage.root / "janitor-status.json")
    payload = dict(
        at=utc(janitor.now()).isoformat(),
        ok=not report.errors,
        counts={key: value for key, value in asdict(report).items() if key != "errors"},
        error_count=len(report.errors),
    )
    write_atomically(path, lambda handle: handle.write(json.dumps(payload).encode()))


def run_loop(janitor: Janitor, stop: Event, *, monotonic: Callable[[], float] = time.monotonic) -> None:
    next_maintenance = 0.0
    interval = min(30.0, janitor.settings.job_lease_seconds / 3)
    while not stop.is_set():
        start = monotonic()
        maintenance = start >= next_maintenance
        try:
            report = janitor.run_once(maintenance=maintenance)
            heartbeat(janitor, report)
            LOG.info("janitor_cycle", **asdict(report))
            if maintenance:
                next_maintenance = start + janitor.settings.storage_cleanup_interval_hours * 3600
        except (OSError, ValueError, SQLAlchemyError):
            LOG.exception("janitor_cycle_failed")
            # Leave the old heartbeat unchanged: freshness checks report this failure.
        stop.wait(max(0, interval - (monotonic() - start)))
