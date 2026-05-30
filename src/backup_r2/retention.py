"""GFS (grandfather-father-son) retention selection.

Mirrors the well-understood semantics of restic/borg: each ``keep_*`` rule
independently selects backups, and a backup survives pruning if it is selected
by *any* rule (the union). Rules that count periods (daily/weekly/monthly/
yearly) keep the most recent backup from each of the last N periods that
actually contain a backup, so an intermittent source never causes wrongful
deletion.
"""

from __future__ import annotations

from datetime import datetime

from .backups import Backup
from .config import RetentionConfig


def _period_key(rule: str, dt: datetime) -> tuple:
    if rule == "daily":
        return (dt.year, dt.month, dt.day)
    if rule == "weekly":
        iso = dt.isocalendar()
        return (iso.year, iso.week)
    if rule == "monthly":
        return (dt.year, dt.month)
    if rule == "yearly":
        return (dt.year,)
    raise ValueError(f"Unknown period rule: {rule}")


def _select_periodic(backups: list[Backup], rule: str, count: int) -> set[str]:
    """Keep the most recent backup from each of the last ``count`` periods."""
    if count <= 0:
        return set()
    kept: set[str] = set()
    seen_periods: set[tuple] = set()
    for backup in backups:  # assumed newest-first
        period = _period_key(rule, backup.timestamp)
        if period in seen_periods:
            continue
        if len(seen_periods) >= count:
            break
        seen_periods.add(period)
        kept.add(backup.key)
    return kept


def select_keep(backups: list[Backup], retention: RetentionConfig) -> set[str]:
    """Return the set of keys to retain under the configured rules."""
    ordered = sorted(backups, key=lambda b: (b.timestamp, b.key), reverse=True)

    keep: set[str] = set()
    if retention.keep_last > 0:
        keep.update(b.key for b in ordered[: retention.keep_last])
    keep |= _select_periodic(ordered, "daily", retention.keep_daily)
    keep |= _select_periodic(ordered, "weekly", retention.keep_weekly)
    keep |= _select_periodic(ordered, "monthly", retention.keep_monthly)
    keep |= _select_periodic(ordered, "yearly", retention.keep_yearly)
    return keep


def select_prune(backups: list[Backup], retention: RetentionConfig) -> list[Backup]:
    """Return the backups to delete (everything not selected by any rule)."""
    keep = select_keep(backups, retention)
    return [b for b in backups if b.key not in keep]
