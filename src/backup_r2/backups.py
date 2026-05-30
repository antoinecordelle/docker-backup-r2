"""Discovery of local backup files and timestamp extraction.

A :class:`Backup` is the common unit handled throughout the system. It carries
a stable ``key`` (the object name used in R2) and a ``timestamp`` used both for
ordering uploads and for the GFS retention buckets.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# Formats attempted when no explicit BACKUP_TIMESTAMP_FORMAT is configured.
# Covers ISO-ish names that are filesystem-safe (colons replaced by dashes).
_AUTO_FORMATS = (
    "%Y-%m-%dT%H-%M-%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d_%H-%M-%S",
    "%Y-%m-%d-%H-%M-%S",
    "%Y%m%dT%H%M%S",
    "%Y%m%d-%H%M%S",
    "%Y%m%d%H%M%S",
    "%Y-%m-%d",
    "%Y%m%d",
)


@dataclass(frozen=True)
class Backup:
    """A backup, either discovered locally or listed from R2."""

    key: str
    timestamp: datetime
    size: int
    # Set for local files awaiting upload; None for objects already in R2.
    local_path: Path | None = None
    # True when ``timestamp`` came from the filename, False when it is a
    # fallback (mtime / R2 LastModified). Used to warn about unreliable buckets.
    timestamp_parsed: bool = True


def _normalize(dt: datetime) -> datetime:
    """Ensure every timestamp is timezone-aware (UTC) for safe comparison."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_timestamp(
    name: str,
    *,
    regex: re.Pattern[str] | None,
    fmt: str | None,
    fallback: datetime,
) -> tuple[datetime, bool]:
    """Extract a timestamp from ``name``.

    Returns ``(timestamp, parsed)`` where ``parsed`` is False when the regex did
    not match or the captured text could not be parsed, in which case
    ``fallback`` (mtime / LastModified) is returned instead.
    """
    if regex is None:
        return _normalize(fallback), False

    match = regex.search(name)
    if not match:
        log.warning("Timestamp regex did not match %r; falling back to mtime/LastModified", name)
        return _normalize(fallback), False

    # Prefer a named group, then the first capture group, then the whole match.
    captured = match.groupdict().get("ts")
    if captured is None:
        captured = match.group(1) if match.groups() else match.group(0)

    parsed = _parse_text(captured, fmt)
    if parsed is None:
        log.warning("Could not parse timestamp %r from %r; falling back", captured, name)
        return _normalize(fallback), False
    return _normalize(parsed), True


def _parse_text(text: str, fmt: str | None) -> datetime | None:
    if fmt:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    for candidate in _AUTO_FORMATS:
        try:
            return datetime.strptime(text, candidate)
        except ValueError:
            continue
    return None


def discover_local_backups(
    backup_dir: str,
    glob: str,
    *,
    regex: re.Pattern[str] | None,
    fmt: str | None,
    prefix: str = "",
) -> list[Backup]:
    """Find local backup files and build :class:`Backup` records.

    The object ``key`` is ``prefix + filename`` so timestamped names are
    preserved verbatim in R2.
    """
    directory = Path(backup_dir)
    if not directory.is_dir():
        log.warning("Backup directory %s does not exist or is not a directory", backup_dir)
        return []

    backups: list[Backup] = []
    for path in sorted(directory.glob(glob)):
        if not path.is_file():
            continue
        stat = path.stat()
        fallback = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        ts, parsed = parse_timestamp(path.name, regex=regex, fmt=fmt, fallback=fallback)
        backups.append(
            Backup(
                key=f"{prefix}{path.name}",
                timestamp=ts,
                size=stat.st_size,
                local_path=path,
                timestamp_parsed=parsed,
            )
        )
    return backups
