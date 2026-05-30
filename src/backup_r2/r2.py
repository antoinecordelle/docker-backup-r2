"""Thin boto3 wrapper around a Cloudflare R2 (S3-compatible) bucket."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from .backups import Backup, parse_timestamp

log = logging.getLogger(__name__)


class R2Client:
    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=BotoConfig(
                retries={"max_attempts": 5, "mode": "standard"},
                s3={"addressing_style": "path"},
            ),
        )

    def list_backups(
        self,
        prefix: str,
        *,
        regex: re.Pattern[str] | None,
        fmt: str | None,
    ) -> list[Backup]:
        """List objects under ``prefix`` as :class:`Backup` records.

        The timestamp is parsed from the key when possible, otherwise the
        object's LastModified is used as the fallback.
        """
        backups: list[Backup] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                # Skip "directory" placeholder keys.
                if key.endswith("/"):
                    continue
                name = key[len(prefix):] if prefix and key.startswith(prefix) else key
                last_modified = obj.get("LastModified") or datetime.now(tz=timezone.utc)
                ts, parsed = parse_timestamp(name, regex=regex, fmt=fmt, fallback=last_modified)
                backups.append(
                    Backup(
                        key=key,
                        timestamp=ts,
                        size=obj.get("Size", 0),
                        local_path=None,
                        timestamp_parsed=parsed,
                    )
                )
        return backups

    def upload(self, local_path: Path, key: str) -> None:
        """Upload a file; boto3 auto-multiparts large objects."""
        self._client.upload_file(str(local_path), self.bucket, key)

    def delete(self, keys: list[str]) -> None:
        """Delete objects in batches of up to 1000 (the S3 API limit)."""
        for start in range(0, len(keys), 1000):
            batch = keys[start:start + 1000]
            self._client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in batch]},
            )
