import re
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from backup_r2.config import Config, RetentionConfig
from backup_r2.r2 import R2Client
from backup_r2.sync import run_cycle

BUCKET = "backups"
ISO_RE = r"(\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2})"
BASE = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)


def _make_config(tmp_path, **overrides) -> Config:
    base = dict(
        account_id="acct",
        access_key_id="key",
        secret_access_key="secret",
        bucket=BUCKET,
        prefix="",
        backup_dir=str(tmp_path),
        backup_glob="*.tar.gz",
        timestamp_regex=ISO_RE,
        timestamp_format=None,
        retention=RetentionConfig(keep_last=3),
    )
    base.update(overrides)
    return Config(**base)


def _write_backups(tmp_path, days):
    """Create timestamped local files for the given day offsets from BASE."""
    names = []
    for d in days:
        ts = BASE - timedelta(days=d)
        name = ts.strftime("backup-%Y-%m-%dT%H-%M-%S.tar.gz")
        (tmp_path / name).write_bytes(b"x" * (d + 1))
        names.append(name)
    return names


def _client_and_bucket():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    # moto only intercepts AWS-domain hosts, so use the standard S3 endpoint
    # here. The client code path is identical to the R2 endpoint in production.
    r2 = R2Client(
        endpoint_url="https://s3.amazonaws.com",
        access_key_id="key",
        secret_access_key="secret",
        bucket=BUCKET,
    )
    return s3, r2


def _keys(s3):
    resp = s3.list_objects_v2(Bucket=BUCKET)
    return sorted(o["Key"] for o in resp.get("Contents", []))


@mock_aws
def test_initial_upload_then_prune(tmp_path):
    names = _write_backups(tmp_path, [0, 1, 2, 3, 4])  # 5 backups, keep_last=3
    s3, r2 = _client_and_bucket()
    config = _make_config(tmp_path)

    result = run_cycle(config, r2)

    assert len(result.uploaded) == 5
    assert len(result.pruned) == 2
    # Only the 3 most recent remain.
    expected = sorted(names[:3])
    assert _keys(s3) == expected


@mock_aws
def test_second_run_is_noop(tmp_path):
    _write_backups(tmp_path, [0, 1, 2])
    s3, r2 = _client_and_bucket()
    config = _make_config(tmp_path)

    run_cycle(config, r2)
    keys_after_first = _keys(s3)
    result = run_cycle(config, r2)

    assert result.uploaded == []
    assert result.already_present == 3
    assert result.pruned == []
    assert _keys(s3) == keys_after_first


@mock_aws
def test_source_rotation_does_not_delete_from_r2(tmp_path):
    # Upload 3, then the source "rotates": old local files gone, a new one appears.
    _write_backups(tmp_path, [2, 3, 4])
    s3, r2 = _client_and_bucket()
    config = _make_config(tmp_path, retention=RetentionConfig(keep_last=10))
    run_cycle(config, r2)
    assert len(_keys(s3)) == 3

    # Simulate source keeping only newest on disk: remove all, add a newer one.
    for f in tmp_path.glob("*.tar.gz"):
        f.unlink()
    _write_backups(tmp_path, [0])

    result = run_cycle(config, r2)
    assert len(result.uploaded) == 1
    # R2 still has the originals plus the new one — nothing deleted (under floor).
    assert len(_keys(s3)) == 4


@mock_aws
def test_dry_run_uploads_nothing(tmp_path):
    _write_backups(tmp_path, [0, 1, 2, 3])
    s3, r2 = _client_and_bucket()
    config = _make_config(tmp_path, dry_run=True)

    result = run_cycle(config, r2)
    assert len(result.uploaded) == 4  # reported as would-upload
    assert _keys(s3) == []  # but nothing actually written


@mock_aws
def test_prefix_safety_ignores_unrelated_objects(tmp_path):
    s3, r2 = _client_and_bucket()
    # An unrelated object sharing no glob match.
    s3.put_object(Bucket=BUCKET, Key="important-do-not-touch.txt", Body=b"keep me")
    _write_backups(tmp_path, [0, 1, 2, 3, 4])
    config = _make_config(tmp_path)  # keep_last=3

    run_cycle(config, r2)

    keys = _keys(s3)
    assert "important-do-not-touch.txt" in keys
    # 3 backups + the unrelated file
    assert len(keys) == 4


@mock_aws
def test_reupload_on_size_mismatch(tmp_path):
    name = _write_backups(tmp_path, [0])[0]
    s3, r2 = _client_and_bucket()
    # Pre-seed R2 with a truncated/partial object of a different size.
    s3.put_object(Bucket=BUCKET, Key=name, Body=b"")
    config = _make_config(tmp_path)

    result = run_cycle(config, r2)
    assert name in result.uploaded
    obj = s3.get_object(Bucket=BUCKET, Key=name)
    assert obj["ContentLength"] == 1  # re-uploaded with correct content
