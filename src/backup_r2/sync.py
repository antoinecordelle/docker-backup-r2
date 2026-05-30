"""Reconcile local backups with R2: upload new ones, prune per GFS rules.

The cycle is stateless and idempotent. It reconciles against the live R2
listing each run, so a restarted container or a long-idle source both converge
correctly:

  1. discover local files and list R2 objects
  2. upload local files missing from R2 (or whose size differs)
  3. prune R2 down to the GFS-selected set

Uploads are additive and never cause deletions; pruning is driven solely by the
retention rules applied to the R2 listing.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass, field

from .backups import Backup, discover_local_backups
from .config import Config
from .r2 import R2Client
from .retention import select_prune

log = logging.getLogger(__name__)


@dataclass
class SyncResult:
    local: int = 0
    already_present: int = 0
    uploaded: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    errors: int = 0

    def summary(self, dry_run: bool) -> str:
        prefix = "[dry-run] would " if dry_run else ""
        return (
            f"local={self.local} already-in-R2={self.already_present} "
            f"{prefix}uploaded={len(self.uploaded)} {prefix}pruned={len(self.pruned)} "
            f"errors={self.errors}"
        )


def run_cycle(config: Config, client: R2Client) -> SyncResult:
    regex = config.compiled_timestamp_regex
    fmt = config.timestamp_format
    result = SyncResult()

    local = discover_local_backups(
        config.backup_dir,
        config.backup_glob,
        regex=regex,
        fmt=fmt,
        prefix=config.prefix,
    )
    result.local = len(local)

    remote = client.list_backups(config.prefix, regex=regex, fmt=fmt)
    # Safety guard: only treat objects matching the backup glob as backups, so
    # pruning can never touch unrelated objects that share the prefix.
    remote = [b for b in remote if fnmatch.fnmatch(_basename(b.key, config.prefix), config.backup_glob)]
    remote_by_key = {b.key: b for b in remote}

    if any(not b.timestamp_parsed for b in local + remote) and _periodic_enabled(config):
        log.warning(
            "Some backups have no parseable filename timestamp; periodic "
            "(daily/weekly/monthly/yearly) buckets may be unreliable. "
            "Check BACKUP_TIMESTAMP_REGEX / BACKUP_TIMESTAMP_FORMAT."
        )

    # 1) Upload local backups that are new or whose size differs (interrupted upload).
    to_upload: list[Backup] = []
    for backup in local:
        existing = remote_by_key.get(backup.key)
        if existing is None:
            to_upload.append(backup)
        elif existing.size != backup.size:
            log.info("Size mismatch for %s (local=%d r2=%d); re-uploading",
                     backup.key, backup.size, existing.size)
            to_upload.append(backup)
        else:
            result.already_present += 1

    for backup in to_upload:
        if config.dry_run:
            log.info("[dry-run] would upload %s (%d bytes)", backup.key, backup.size)
            result.uploaded.append(backup.key)
            remote_by_key[backup.key] = backup
            continue
        try:
            log.info("Uploading %s (%d bytes)", backup.key, backup.size)
            client.upload(backup.local_path, backup.key)
            result.uploaded.append(backup.key)
            remote_by_key[backup.key] = backup
        except Exception:  # noqa: BLE001 - log and keep going so one bad file isn't fatal
            log.exception("Failed to upload %s", backup.key)
            result.errors += 1

    # 2) Prune R2 down to the retained set. Operates on the post-upload view.
    post_upload = list(remote_by_key.values())
    prune = select_prune(post_upload, config.retention)
    prune_keys = [b.key for b in prune]

    if prune_keys:
        if config.dry_run:
            for key in prune_keys:
                log.info("[dry-run] would prune %s", key)
        else:
            log.info("Pruning %d backup(s): %s", len(prune_keys), ", ".join(prune_keys))
            try:
                client.delete(prune_keys)
            except Exception:  # noqa: BLE001
                log.exception("Failed to prune objects")
                result.errors += 1
                prune_keys = []
        result.pruned = prune_keys

    log.info("Cycle complete: %s", result.summary(config.dry_run))
    return result


def _basename(key: str, prefix: str) -> str:
    return key[len(prefix):] if prefix and key.startswith(prefix) else key


def _periodic_enabled(config: Config) -> bool:
    r = config.retention
    return any((r.keep_daily, r.keep_weekly, r.keep_monthly, r.keep_yearly))
