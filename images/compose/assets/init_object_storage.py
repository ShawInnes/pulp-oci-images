#!/usr/bin/env python3
"""Create Pulp's private S3-compatible storage bucket without changing existing buckets."""

import json
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def configuration():
    return {
        "endpoint_url": os.environ.get("PULP_S3_ENDPOINT_URL", "http://rustfs:9000"),
        "bucket": os.environ.get("PULP_S3_BUCKET", "pulp"),
        "access_key": os.environ["PULP_S3_ACCESS_KEY"],
        "secret_key": os.environ["PULP_S3_SECRET_KEY"],
        "region": os.environ.get("PULP_S3_REGION", "us-east-1"),
    }


def client(config):
    return boto3.client(
        "s3",
        endpoint_url=config["endpoint_url"],
        aws_access_key_id=config["access_key"],
        aws_secret_access_key=config["secret_key"],
        region_name=config["region"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


def missing(error):
    response = error.response
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    return status == 404 or (
        status is None
        and str(response.get("Error", {}).get("Code"))
        in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}
    )


def initialize(s3, bucket, region):
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        if not missing(error):
            raise
        options = {"Bucket": bucket}
        if region != "us-east-1":
            options["CreateBucketConfiguration"] = {"LocationConstraint": region}
        # S3 buckets are private by default; no public ACL or bucket policy is added.
        s3.create_bucket(**options)
        return {"created": True}
    return {"created": False}


def failure(error):
    if isinstance(error, ClientError):
        return "S3 request failed: " + str(error.response.get("Error", {}).get("Code", "error"))
    if isinstance(error, BotoCoreError):
        return "S3 connection failed: " + type(error).__name__
    return str(error)


def main():
    try:
        config = configuration()
        print(json.dumps(initialize(client(config), config["bucket"], config["region"])))
    except (KeyError, OSError, ValueError, BotoCoreError, ClientError) as error:
        print("Object storage initialization failed: " + failure(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
