"""JSON service logs and append-only, best-effort journals for fenced attempts."""

from __future__ import annotations

import json
import logging
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, TextIO

import structlog
from structlog.contextvars import get_contextvars

from hephaistos.queue.broker import Lease, utc
from hephaistos.storage.local import LocalStorage, UnsafePath

_RECORD_KEYS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
LOG = logging.getLogger(__name__)


def json_line(payload: dict[str, Any]) -> str:
    safe = {}
    for key, value in payload.items():
        try:
            json.dumps(value, default=str, allow_nan=False)
        except (TypeError, ValueError, RecursionError):
            value = repr(value)
        safe[key] = value
    return json.dumps(safe, default=str, ensure_ascii=False, allow_nan=False)


class JSONFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        fields = {key: value for key, value in record.__dict__.items() if key not in _RECORD_KEYS}
        payload = dict(get_contextvars(), **fields)
        payload.update(
            event=record.getMessage(),
            level=record.levelname.lower(),
            service=self.service,
            timestamp=fields.get("timestamp") or datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            logger=record.name,
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json_line(payload)


class _JSONHandler(logging.StreamHandler[TextIO]):
    pass


def configure_logging(service: str, *, stream: TextIO | None = None) -> None:
    """Keep host capture handlers; replace only our own stdout handler on repeated setup."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if isinstance(handler, _JSONHandler):
            root.removeHandler(handler)
            handler.close()
    handler = _JSONHandler(stream or sys.stdout)
    handler.setFormatter(JSONFormatter(service))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    for name, logger in logging.Logger.manager.loggerDict.items():
        if (name == "hephaistos" or name.startswith("hephaistos.")) and isinstance(logger, logging.Logger):
            logger.disabled = False
            logger.setLevel(logging.NOTSET)
            logger.propagate = True
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # The API emits access events with correlation and without query/body contents.
    logging.getLogger("uvicorn.access").disabled = True
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.format_exc_info,
            structlog.stdlib.render_to_log_kwargs,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


@contextmanager
def logging_context(service: str, *, stream: TextIO | None = None) -> Iterator[None]:
    """Restore the embedding application's logging after an API lifespan or a test."""
    root = logging.getLogger()
    handlers, level, config = list(root.handlers), root.level, structlog.get_config().copy()
    app_loggers = {
        name: (logger.level, logger.disabled, logger.propagate)
        for name, logger in logging.Logger.manager.loggerDict.items()
        if (name == "hephaistos" or name.startswith("hephaistos.")) and isinstance(logger, logging.Logger)
    }
    uvicorn = {
        name: (
            list(logging.getLogger(name).handlers),
            logging.getLogger(name).propagate,
            logging.getLogger(name).disabled,
        )
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access")
    }
    configure_logging(service, stream=stream)
    try:
        yield
    finally:
        for handler in root.handlers:
            if handler not in handlers:
                handler.close()
        root.handlers = handlers
        root.setLevel(level)
        structlog.configure(**config)
        for name, (logger_level, disabled, propagate) in app_loggers.items():
            logger = logging.getLogger(name)
            logger.setLevel(logger_level)
            logger.disabled, logger.propagate = disabled, propagate
        for name, (saved_handlers, propagate, disabled) in uvicorn.items():
            logger = logging.getLogger(name)
            logger.handlers, logger.propagate, logger.disabled = saved_handlers, propagate, disabled


class AttemptJournal:
    def __init__(
        self, storage: LocalStorage, lease: Lease, *, now: Callable[[], datetime] | None = None, service: str = "worker"
    ) -> None:
        self.storage, self.lease, self.service = storage, lease, service
        self.now = now or (lambda: datetime.now(UTC))

    def write(self, event: str, **fields: Any) -> bool:
        lease = self.lease
        context = dict(
            generation_id=lease.generation_id,
            attempt_id=lease.attempt_id,
            attempt=lease.number,
            worker_id=lease.worker_id,
            model_id=lease.model_id,
        )
        payload = dict(
            context,
            event=event,
            fields=fields,
            service=self.service,
            level="info",
            timestamp=utc(self.now()).isoformat().replace("+00:00", "Z"),
        )
        LOG.info(event, extra={key: value for key, value in payload.items() if key != "event"})
        try:
            path = self.storage.log_file(lease.generation_id, lease.number)
            path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(path, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json_line(payload) + "\n")
            return True
        except (OSError, UnsafePath, ValueError) as exc:
            LOG.warning("attempt_journal_unavailable", extra=dict(context, error_type=type(exc).__name__))
            return False
