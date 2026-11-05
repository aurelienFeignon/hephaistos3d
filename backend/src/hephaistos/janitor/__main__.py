"""Run with python -m hephaistos.janitor [--once]. Migrations remain explicit."""

import argparse
import signal
from threading import Event

from hephaistos.config import load_settings
from hephaistos.janitor.runner import heartbeat, run_loop
from hephaistos.janitor.service import Janitor
from hephaistos.persistence.database import create_database_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Recover interrupted jobs and maintain local storage without CUDA")
    parser.add_argument("--once", action="store_true", help="Run one complete maintenance cycle and exit")
    args = parser.parse_args()
    settings = load_settings()
    engine = create_database_engine(settings.hephaistos_data_dir)
    try:
        janitor = Janitor(engine, settings)
        if args.once:
            report = janitor.run_once()
            heartbeat(janitor, report)
            return int(bool(report.errors))
        stop = Event()

        def shutdown(_signum: int, _frame: object) -> None:
            stop.set()

        signal.signal(signal.SIGTERM, shutdown)
        signal.signal(signal.SIGINT, shutdown)
        run_loop(janitor, stop)
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
