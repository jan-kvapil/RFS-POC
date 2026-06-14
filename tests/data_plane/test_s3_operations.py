"""Data plane tests — S3 API via boto3."""

import logging
import uuid

import pytest
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

pytestmark = pytest.mark.data_plane


def _unique_key(prefix: str = "obj") -> str:
    """Generate unique object key."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


class TestBucketOperations:
    """Bucket create/delete lifecycle."""

    def test_create_and_delete_bucket(self, s3_client):
        """Create bucket, verify exists, delete, verify gone."""
        bucket_name = f"test-create-{uuid.uuid4().hex[:8]}"
        log.info("Bucket: %s", bucket_name)

        log.info("Creating bucket...")
        response = s3_client.create_bucket(Bucket=bucket_name)
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

        log.info("Verifying bucket exists...")
        head = s3_client.head_bucket(Bucket=bucket_name)
        assert head["ResponseMetadata"]["HTTPStatusCode"] == 200

        log.info("Deleting bucket...")
        s3_client.delete_bucket(Bucket=bucket_name)

        log.info("Verifying bucket gone...")
        with pytest.raises(ClientError) as exc_info:
            s3_client.head_bucket(Bucket=bucket_name)
        assert exc_info.value.response["Error"]["Code"] in ("404", "NoSuchBucket")
        log.info("PASS")


class TestObjectOperations:
    """Object CRUD using shared test bucket."""

    def test_put_and_get_object(self, s3_client, test_bucket):
        """PUT then GET — content must match exactly."""
        key = _unique_key("put-get")
        content = b"Hello, RustFS! This is test content."
        log.info("Key: %s", key)

        log.info("PUT %d bytes...", len(content))
        s3_client.put_object(Bucket=test_bucket, Key=key, Body=content)

        log.info("GET...")
        retrieved = s3_client.get_object(Bucket=test_bucket, Key=key)["Body"].read()

        assert retrieved == content
        log.info("PASS: content matches")

    def test_put_object_with_metadata(self, s3_client, test_bucket):
        """PUT with custom metadata, verify via HEAD."""
        key = _unique_key("metadata")
        metadata = {"author": "test-suite", "environment": "ci"}
        log.info("Key: %s | Metadata: %s", key, metadata)

        log.info("PUT with metadata...")
        s3_client.put_object(Bucket=test_bucket, Key=key, Body=b"metadata test", Metadata=metadata)

        log.info("HEAD...")
        returned = s3_client.head_object(Bucket=test_bucket, Key=key)["Metadata"]
        log.info("Returned: %s", returned)

        for k, v in metadata.items():
            assert returned[k] == v
        log.info("PASS: metadata matches")

    def test_list_objects(self, s3_client, test_bucket):
        """PUT 3 objects, verify all appear in list_objects_v2."""
        prefix = f"list-test-{uuid.uuid4().hex[:6]}"
        keys = [f"{prefix}/file-{i}.txt" for i in range(3)]
        log.info("Prefix: %s", prefix)

        for key in keys:
            s3_client.put_object(Bucket=test_bucket, Key=key, Body=b"list test")
            log.info("PUT %s", key)

        log.info("LIST prefix=%s...", prefix)
        response = s3_client.list_objects_v2(Bucket=test_bucket, Prefix=prefix)
        listed = [obj["Key"] for obj in response.get("Contents", [])]

        for key in keys:
            assert key in listed, f"{key} not in listing"
        assert response["KeyCount"] >= 3
        log.info("PASS: all 3 keys found")

    def test_delete_object(self, s3_client, test_bucket):
        """PUT, DELETE, verify GET raises NoSuchKey."""
        key = _unique_key("delete")
        log.info("Key: %s", key)

        log.info("PUT (setup)...")
        s3_client.put_object(Bucket=test_bucket, Key=key, Body=b"to be deleted")

        log.info("DELETE...")
        s3_client.delete_object(Bucket=test_bucket, Key=key)

        log.info("GET (expect NoSuchKey)...")
        with pytest.raises(ClientError) as exc_info:
            s3_client.get_object(Bucket=test_bucket, Key=key)
        assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"
        log.info("PASS")

    def test_get_nonexistent_object(self, s3_client, test_bucket):
        """GET non-existent key — expect NoSuchKey."""
        key = f"does-not-exist-{uuid.uuid4().hex[:8]}"
        log.info("Key: %s (never PUT)", key)

        with pytest.raises(ClientError) as exc_info:
            s3_client.get_object(Bucket=test_bucket, Key=key)

        assert exc_info.value.response["Error"]["Code"] == "NoSuchKey"
        log.info("PASS")

    def test_overwrite_object(self, s3_client, test_bucket):
        """PUT A, PUT B to same key, GET returns B."""
        key = _unique_key("overwrite")
        content_a = b"original content"
        content_b = b"overwritten content"
        log.info("Key: %s", key)

        log.info("PUT original...")
        s3_client.put_object(Bucket=test_bucket, Key=key, Body=content_a)

        log.info("PUT overwrite...")
        s3_client.put_object(Bucket=test_bucket, Key=key, Body=content_b)

        log.info("GET...")
        retrieved = s3_client.get_object(Bucket=test_bucket, Key=key)["Body"].read()

        assert retrieved == content_b
        assert retrieved != content_a
        log.info("PASS: overwritten")
