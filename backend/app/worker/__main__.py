"""Standalone worker entry point: ``python -m app.worker``."""

import logging
import signal
import threading

from app.core.logging import configure_logging
from app.worker.runner import run_forever


def main() -> None:
    configure_logging()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    logging.getLogger("contractlens.worker").info("Starting standalone worker")
    run_forever(stop)


if __name__ == "__main__":
    main()
