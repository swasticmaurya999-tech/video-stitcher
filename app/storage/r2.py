"""Cloudflare R2 storage backend (S3-compatible via boto3).

Identical interface to LocalStorage — the only difference from AWS S3 is the endpoint + creds,
so this also works against literal S3 if ever needed. Zero egress on R2 makes the
download-to-temp round trips free.
"""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import BinaryIO

import boto3
from botocore.client import Config

from app.config import settings


def _content_type(key: str) -> str:
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


class R2Storage:
    def __init__(self) -> None:
        self.bucket = settings.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.r2_endpoint,
            aws_access_key_id=settings.r2_access_key,
            aws_secret_access_key=settings.r2_secret_key,
            config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
            region_name="auto",
        )

    def _retry(self, fn, *a, **k):
        last = None
        for attempt in range(3):
            try:
                return fn(*a, **k)
            except Exception as e:  # transient network/5xx
                last = e
                time.sleep(0.5 * (attempt + 1))
        raise last

    def save_stream(self, key: str, stream: BinaryIO) -> int:
        # boto3 upload_fileobj streams in parts; it doesn't return a count, so we don't rely on it.
        self._retry(
            self.client.upload_fileobj, stream, self.bucket, key,
            ExtraArgs={"ContentType": _content_type(key)},
        )
        return self._head_size(key)

    def save_file(self, key: str, local_path: str) -> None:
        self._retry(
            self.client.upload_file, local_path, self.bucket, key,
            ExtraArgs={"ContentType": _content_type(key)},
        )

    def download(self, key: str, local_path: str) -> None:
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        self._retry(self.client.download_file, self.bucket, key, local_path)

    def presigned_get(self, key: str, expires: int) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
        )

    def delete_prefix(self, prefix: str) -> None:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if objs:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": objs})

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def _head_size(self, key: str) -> int:
        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except Exception:
            return 0
