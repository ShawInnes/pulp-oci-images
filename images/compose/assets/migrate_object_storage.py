#!/usr/bin/env python3
"""Copy a stopped Pulp media tree into S3, preserving keys and verifying every byte."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from botocore.exceptions import BotoCoreError, ClientError

from init_object_storage import client, configuration, failure, missing

CHUNK_SIZE = 8 * 1024 * 1024


def digest(stream):
    hasher = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK_SIZE):
        hasher.update(chunk)
        size += len(chunk)
    return size, hasher.hexdigest()


def files(source):
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Source must be a real media directory")
    for directory, directories, names in os.walk(source, followlinks=False):
        directories.sort()
        for name in sorted(directories + names):
            if (Path(directory) / name).is_symlink():
                raise ValueError("Refusing media symlink: " + str((Path(directory) / name).relative_to(source)))
        for name in sorted(names):
            path = Path(directory) / name
            if not path.is_file():
                raise ValueError("Refusing non-file media entry: " + str(path.relative_to(source)))
            yield path


def verify(s3, bucket, key, expected):
    body = s3.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        actual = digest(body)
    finally:
        body.close()
    if actual != expected:
        raise ValueError("Destination content mismatch: " + key)


def migrate(s3, bucket, source, verify_only=False):
    source = Path(source)
    result = {"files": 0, "bytes": 0, "uploaded": 0, "verified_existing": 0}
    for path in files(source):
        key = path.relative_to(source).as_posix()
        before = path.stat()
        with path.open("rb") as stream:
            expected = digest(stream)
        try:
            remote = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as error:
            if not missing(error):
                raise
            if verify_only:
                raise ValueError("Missing destination object: " + key) from error
            s3.upload_file(str(path), bucket, key)
            result["uploaded"] += 1
        else:
            if remote["ContentLength"] != expected[0]:
                raise ValueError("Destination size mismatch: " + key)
            result["verified_existing"] += 1
        verify(s3, bucket, key, expected)
        after = path.stat()
        if (before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ValueError("Source changed during migration: " + key)
        result["files"] += 1
        result["bytes"] += expected[0]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/var/lib/pulp/media"))
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    try:
        config = configuration()
        print(json.dumps(migrate(client(config), config["bucket"], args.source, args.verify_only)))
    except (KeyError, OSError, ValueError, BotoCoreError, ClientError) as error:
        print("Object storage migration failed: " + failure(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
