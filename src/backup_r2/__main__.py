"""Entrypoint: run one reconcile cycle (RUN_ONCE) or loop on an interval."""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time

from .config import Config, ConfigError, load_config
from .r2 import R2Client
from .sync import run_cycle

log = logging.getLogger("backup_r2")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )


def _make_client(config: Config) -> R2Client:
    return R2Client(
        endpoint_url=config.endpoint_url,
        access_key_id=config.access_key_id,
        secret_access_key=config.secret_access_key,
        bucket=config.bucket,
    )


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        # Logging may not be configured yet; print to stderr.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    _configure_logging(config.log_level)
    log.info(
        "Starting docker-backup-r2 (bucket=%s prefix=%r dir=%s glob=%s dry_run=%s)",
        config.bucket, config.prefix, config.backup_dir, config.backup_glob, config.dry_run,
    )

    client = _make_client(config)

    if config.run_once:
        result = run_cycle(config, client)
        return 1 if result.errors else 0

    # Loop mode: run until SIGTERM/SIGINT, sleeping interruptibly between cycles.
    stop = threading.Event()

    def _handle(signum, _frame):
        log.info("Received signal %s; will stop after current cycle", signum)
        stop.set()

    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)

    log.info("Running in loop mode, interval=%ds", config.interval_seconds)
    while not stop.is_set():
        try:
            run_cycle(config, client)
        except Exception:  # noqa: BLE001 - never let the loop die on a transient error
            log.exception("Unexpected error during cycle; will retry next interval")
        stop.wait(config.interval_seconds)

    log.info("Shutting down")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
