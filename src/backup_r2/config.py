"""Configuration loaded and validated from environment variables."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


class ConfigError(Exception):
    """Raised when the environment does not yield a valid configuration."""


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _get_required(name: str) -> str:
    value = _get(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _get_int(name: str, default: int) -> int:
    raw = _get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} must be >= 0, got {value}")
    return value


def _get_bool(name: str, default: bool) -> bool:
    raw = _get(name)
    if raw is None:
        return default
    return raw.lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class RetentionConfig:
    """GFS retention rules. A backup is kept if any rule selects it."""

    keep_last: int = 0
    keep_daily: int = 0
    keep_weekly: int = 0
    keep_monthly: int = 0
    keep_yearly: int = 0

    @property
    def any_enabled(self) -> bool:
        return any(
            (
                self.keep_last,
                self.keep_daily,
                self.keep_weekly,
                self.keep_monthly,
                self.keep_yearly,
            )
        )


@dataclass(frozen=True)
class Config:
    # R2 / S3 connection
    account_id: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    prefix: str = ""

    # Local source
    backup_dir: str = "/backups"
    backup_glob: str = "*"
    timestamp_regex: str | None = None
    timestamp_format: str | None = None

    # Retention
    retention: RetentionConfig = field(default_factory=RetentionConfig)

    # Scheduling / behaviour
    run_once: bool = False
    interval_seconds: int = 3600
    dry_run: bool = False
    log_level: str = "INFO"

    @property
    def endpoint_url(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"

    @property
    def compiled_timestamp_regex(self) -> re.Pattern[str] | None:
        if not self.timestamp_regex:
            return None
        return re.compile(self.timestamp_regex)


def load_config() -> Config:
    """Build a Config from the process environment, validating as we go."""
    retention = RetentionConfig(
        keep_last=_get_int("KEEP_LAST", 0),
        keep_daily=_get_int("KEEP_DAILY", 0),
        keep_weekly=_get_int("KEEP_WEEKLY", 0),
        keep_monthly=_get_int("KEEP_MONTHLY", 0),
        keep_yearly=_get_int("KEEP_YEARLY", 0),
    )
    if not retention.any_enabled:
        raise ConfigError(
            "No retention rule configured. Set at least one of "
            "KEEP_LAST/KEEP_DAILY/KEEP_WEEKLY/KEEP_MONTHLY/KEEP_YEARLY."
        )

    timestamp_regex = _get("BACKUP_TIMESTAMP_REGEX")
    if timestamp_regex:
        try:
            re.compile(timestamp_regex)
        except re.error as exc:
            raise ConfigError(f"BACKUP_TIMESTAMP_REGEX is not a valid regex: {exc}") from exc

    prefix = _get("R2_PREFIX", "") or ""

    return Config(
        account_id=_get_required("R2_ACCOUNT_ID"),
        access_key_id=_get_required("R2_ACCESS_KEY_ID"),
        secret_access_key=_get_required("R2_SECRET_ACCESS_KEY"),
        bucket=_get_required("R2_BUCKET"),
        prefix=prefix,
        backup_dir=_get("BACKUP_DIR", "/backups") or "/backups",
        backup_glob=_get("BACKUP_GLOB", "*") or "*",
        timestamp_regex=timestamp_regex,
        timestamp_format=_get("BACKUP_TIMESTAMP_FORMAT"),
        retention=retention,
        run_once=_get_bool("RUN_ONCE", False),
        interval_seconds=_get_int("INTERVAL_SECONDS", 3600),
        dry_run=_get_bool("DRY_RUN", False),
        log_level=(_get("LOG_LEVEL", "INFO") or "INFO").upper(),
    )
