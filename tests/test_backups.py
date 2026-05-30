import re
from datetime import datetime, timezone

import pytest

from backup_r2.backups import discover_local_backups, parse_timestamp


ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})")
FALLBACK = datetime(2000, 1, 1, tzinfo=timezone.utc)


def test_parse_timestamp_from_dashed_iso():
    ts, parsed = parse_timestamp(
        "backup-2026-05-29T12-00-00.tar.gz", regex=ISO_RE, fmt=None, fallback=FALLBACK
    )
    assert parsed is True
    assert ts == datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_timestamp_named_group_and_format():
    regex = re.compile(r"(?P<ts>\d{8}-\d{6})")
    ts, parsed = parse_timestamp(
        "db_20260529-120000.sql", regex=regex, fmt="%Y%m%d-%H%M%S", fallback=FALLBACK
    )
    assert parsed is True
    assert ts == datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_timestamp_no_match_falls_back():
    ts, parsed = parse_timestamp("weird-name.bin", regex=ISO_RE, fmt=None, fallback=FALLBACK)
    assert parsed is False
    assert ts == FALLBACK


def test_parse_timestamp_no_regex_uses_fallback():
    ts, parsed = parse_timestamp("anything", regex=None, fmt=None, fallback=FALLBACK)
    assert parsed is False
    assert ts == FALLBACK


def test_discover_local_backups(tmp_path):
    for name in ("backup-2026-05-29T12-00-00.tar.gz", "backup-2026-05-28T12-00-00.tar.gz"):
        (tmp_path / name).write_bytes(b"data")
    (tmp_path / "ignore.txt").write_bytes(b"x")

    backups = discover_local_backups(
        str(tmp_path), "*.tar.gz", regex=ISO_RE, fmt=None, prefix="prod/"
    )
    assert {b.key for b in backups} == {
        "prod/backup-2026-05-29T12-00-00.tar.gz",
        "prod/backup-2026-05-28T12-00-00.tar.gz",
    }
    assert all(b.local_path is not None and b.size == 4 for b in backups)
    assert all(b.timestamp_parsed for b in backups)


def test_discover_missing_dir_returns_empty():
    assert discover_local_backups("/nonexistent/xyz", "*", regex=None, fmt=None) == []
