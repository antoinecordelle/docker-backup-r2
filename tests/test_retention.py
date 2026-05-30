from datetime import datetime, timedelta, timezone

from backup_r2.backups import Backup
from backup_r2.config import RetentionConfig
from backup_r2.retention import select_keep, select_prune


def _backup(ts: datetime) -> Backup:
    key = ts.strftime("backup-%Y-%m-%dT%H-%M-%S.tar.gz")
    return Backup(key=key, timestamp=ts, size=1)


def _daily_series(n: int, *, start: datetime, step: timedelta) -> list[Backup]:
    return [_backup(start - step * i) for i in range(n)]


BASE = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def test_keep_last_only():
    backups = _daily_series(10, start=BASE, step=timedelta(days=1))
    keep = select_keep(backups, RetentionConfig(keep_last=3))
    assert keep == {b.key for b in backups[:3]}


def test_fewer_than_n_keeps_all():
    backups = _daily_series(2, start=BASE, step=timedelta(days=1))
    assert select_prune(backups, RetentionConfig(keep_last=5)) == []


def test_empty_input():
    assert select_keep([], RetentionConfig(keep_last=5)) == set()
    assert select_prune([], RetentionConfig(keep_last=5)) == []


def test_keep_daily_one_per_day():
    # Two backups per day for 5 days -> keep_daily=3 keeps the latest of 3 days.
    backups = []
    for day in range(5):
        d = BASE - timedelta(days=day)
        backups.append(_backup(d))
        backups.append(_backup(d - timedelta(hours=6)))
    keep = select_keep(backups, RetentionConfig(keep_daily=3))
    # 3 days kept, the *later* backup of each day.
    assert len(keep) == 3
    for day in range(3):
        d = BASE - timedelta(days=day)
        assert _backup(d).key in keep
        assert _backup(d - timedelta(hours=6)).key not in keep


def test_union_of_rules():
    # Daily backups for 30 days. keep_last=2 + keep_weekly=3.
    backups = _daily_series(30, start=BASE, step=timedelta(days=1))
    keep = select_keep(backups, RetentionConfig(keep_last=2, keep_weekly=3))
    # 2 most recent always kept.
    assert backups[0].key in keep and backups[1].key in keep
    # weekly keeps most recent of 3 distinct ISO weeks; union >= 3 and reasonable.
    assert 3 <= len(keep) <= 5


def test_sparse_timestamps_simulating_downtime():
    # Source was down: backups exist only on a few scattered days/weeks.
    days = [0, 1, 9, 10, 40, 41, 400]
    backups = [_backup(BASE - timedelta(days=d)) for d in days]
    # keep_last=1 + keep_weekly=2 + keep_monthly=2 + keep_yearly=2
    cfg = RetentionConfig(keep_last=1, keep_weekly=2, keep_monthly=2, keep_yearly=2)
    keep = select_keep(backups, cfg)
    # Most recent always survives.
    assert backups[0].key in keep
    # Nothing below the floor is ever fully wiped; some old representatives kept.
    assert backups[-1].key in keep  # the 400-day-old is the only one in its year/month/week tier far back
    # We never keep more than we have.
    assert keep <= {b.key for b in backups}


def test_prune_is_complement_of_keep():
    backups = _daily_series(10, start=BASE, step=timedelta(days=1))
    cfg = RetentionConfig(keep_last=3)
    keep = select_keep(backups, cfg)
    prune = select_prune(backups, cfg)
    assert {b.key for b in prune} == {b.key for b in backups} - keep
    assert len(prune) == 7
