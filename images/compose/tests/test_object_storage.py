"""Temporary-file tests; run with the Pulp image's boto3-enabled Python."""

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

ASSETS = Path(__file__).resolve().parents[1] / "assets"
sys.path.insert(0, str(ASSETS))
import init_object_storage as init
import migrate_object_storage as migration


def error(code, status, operation="HeadObject"):
    return ClientError(
        {"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, operation
    )


class FakeS3:
    def __init__(self):
        self.objects = {}
        self.uploads = []
        self.closed = []

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise error("NoSuchKey", 404)
        return {"ContentLength": len(self.objects[Key])}

    def upload_file(self, Filename, Bucket, Key):
        self.uploads.append(Key)
        self.objects[Key] = Path(Filename).read_bytes()

    def get_object(self, Bucket, Key):
        outer = self

        class Body(io.BytesIO):
            def read(self, size=-1):
                assert 0 < size <= migration.CHUNK_SIZE
                return super().read(size)

            def close(self):
                outer.closed.append(Key)
                super().close()

        return {"Body": Body(self.objects[Key])}


class BucketTest(unittest.TestCase):
    def test_existing_bucket_is_unchanged(self):
        s3 = Mock()
        self.assertEqual(init.initialize(s3, "pulp", "us-east-1"), {"created": False})
        s3.create_bucket.assert_not_called()

    def test_create_missing_private_default_bucket(self):
        s3 = Mock()
        s3.head_bucket.side_effect = error("404", 404, "HeadBucket")
        self.assertEqual(init.initialize(s3, "pulp", "us-east-1"), {"created": True})
        s3.create_bucket.assert_called_once_with(Bucket="pulp")
        s3.put_bucket_policy.assert_not_called()
        s3.put_bucket_acl.assert_not_called()

    def test_other_region_uses_location_constraint(self):
        s3 = Mock()
        s3.head_bucket.side_effect = error("NoSuchBucket", 404, "HeadBucket")
        init.initialize(s3, "pulp", "ap-southeast-2")
        s3.create_bucket.assert_called_once_with(
            Bucket="pulp", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"}
        )

    def test_access_denied_is_not_a_missing_bucket(self):
        s3 = Mock()
        s3.head_bucket.side_effect = error("AccessDenied", 403, "HeadBucket")
        with self.assertRaises(ClientError):
            init.initialize(s3, "pulp", "us-east-1")
        s3.create_bucket.assert_not_called()

    def test_configuration_requires_credentials(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(KeyError):
            init.configuration()

    def test_client_uses_path_style_signed_requests(self):
        config = {"endpoint_url": "http://rustfs:9000", "bucket": "pulp", "access_key": "key",
                  "secret_key": "secret", "region": "us-east-1"}
        with patch.object(init.boto3, "client") as factory:
            init.client(config)
        options = factory.call_args.kwargs
        self.assertEqual(options["endpoint_url"], "http://rustfs:9000")
        self.assertEqual(options["config"].s3, {"addressing_style": "path"})
        self.assertEqual(options["config"].signature_version, "s3v4")


class MigrationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.s3 = FakeS3()

    def write(self, key, value):
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def test_full_tree_preserves_keys_and_originals_and_resumes(self):
        self.write("artifact/aa/digest", b"cached-package")
        self.write("upload/chunk", b"in-progress-chunk")
        self.write("other/.empty", b"")
        result = migration.migrate(self.s3, "pulp", self.root)
        self.assertEqual(result, {"files": 3, "bytes": 31, "uploaded": 3, "verified_existing": 0})
        self.assertEqual(set(self.s3.objects), {"artifact/aa/digest", "upload/chunk", "other/.empty"})
        self.assertTrue((self.root / "artifact/aa/digest").is_file())
        self.s3.uploads.clear()
        result = migration.migrate(self.s3, "pulp", self.root)
        self.assertEqual(result["verified_existing"], 3)
        self.assertEqual(result["uploaded"], 0)
        self.assertFalse(self.s3.uploads)
        self.assertEqual(len(self.s3.closed), 6)

    def test_same_size_corruption_is_not_overwritten(self):
        self.write("artifact/key", b"good")
        self.s3.objects["artifact/key"] = b"evil"
        with self.assertRaisesRegex(ValueError, "content mismatch"):
            migration.migrate(self.s3, "pulp", self.root)
        self.assertEqual(self.s3.objects["artifact/key"], b"evil")
        self.assertFalse(self.s3.uploads)

    def test_size_mismatch_is_not_overwritten(self):
        self.write("artifact/key", b"good")
        self.s3.objects["artifact/key"] = b"different"
        with self.assertRaisesRegex(ValueError, "size mismatch"):
            migration.migrate(self.s3, "pulp", self.root)
        self.assertFalse(self.s3.uploads)

    def test_verify_only_rejects_missing_and_never_uploads(self):
        self.write("artifact/key", b"good")
        with self.assertRaisesRegex(ValueError, "Missing destination"):
            migration.migrate(self.s3, "pulp", self.root, verify_only=True)
        self.assertFalse(self.s3.uploads)

    def test_verify_only_reads_existing_bytes(self):
        self.write("artifact/key", b"good")
        self.s3.objects["artifact/key"] = b"good"
        self.assertEqual(migration.migrate(self.s3, "pulp", self.root, True)["verified_existing"], 1)
        self.assertFalse(self.s3.uploads)
        self.assertEqual(self.s3.closed, ["artifact/key"])

    def test_symlink_directory_and_file_are_rejected(self):
        self.write("original", b"good")
        (self.root / "link").symlink_to(self.root / "original")
        with self.assertRaisesRegex(ValueError, "symlink"):
            migration.migrate(self.s3, "pulp", self.root)
        (self.root / "link").unlink()
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symlink"):
            migration.migrate(self.s3, "pulp", self.root)

    def test_upload_is_verified_before_success(self):
        self.write("artifact/key", b"good")
        original = self.s3.upload_file

        def corrupt(*args):
            original(*args)
            self.s3.objects["artifact/key"] = b"evil"

        self.s3.upload_file = corrupt
        with self.assertRaisesRegex(ValueError, "content mismatch"):
            migration.migrate(self.s3, "pulp", self.root)

    def test_access_denied_does_not_upload(self):
        self.write("artifact/key", b"good")
        self.s3.head_object = Mock(side_effect=error("AccessDenied", 403))
        with self.assertRaises(ClientError):
            migration.migrate(self.s3, "pulp", self.root)
        self.assertFalse(self.s3.uploads)


if __name__ == "__main__":
    unittest.main()
