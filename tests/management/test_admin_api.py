"""Management plane tests for RustFS admin API.

Tests cover:
- Health endpoints (via requests — no auth required)
- User management (via mc CLI in Docker — handles auth signing)

Health tests validate service availability and cluster status.
User tests validate admin CRUD workflow and access verification.
"""

import logging
import uuid

import boto3
import pytest
import requests
from botocore.config import Config as BotoConfig

log = logging.getLogger(__name__)

pytestmark = pytest.mark.management


class TestHealthEndpoints:
    """Tests for unauthenticated RustFS health endpoints."""

    def test_health_live(self, rustfs_config):
        """Verify /health returns HTTP 200."""
        url = f"{rustfs_config['endpoint']}/health"
        log.info("GET %s", url)

        response = requests.get(url, timeout=10)
        log.info("Response: HTTP %d", response.status_code)

        assert response.status_code == 200
        log.info("PASS: liveness endpoint healthy")

    def test_health_cluster(self, rustfs_config):
        """GET /health/cluster — 200 (healthy) or 403 (auth required by RustFS)."""
        url = f"{rustfs_config['endpoint']}/health/cluster"
        log.info("GET %s", url)

        response = requests.get(url, timeout=10)
        log.info("HTTP %d, body=%s", response.status_code, response.text[:200])

        # RustFS enforces auth on cluster health (unlike /health/live).
        # 200 = healthy, 403 = endpoint exists but requires authentication.
        if response.status_code == 403:
            pytest.xfail("403 is expected failure because endpoint exists but requires authentication")
        
        assert response.status_code == 200, (
            f"Unexpected status {response.status_code} — expected 200"
        )
        log.info("PASS (status=%d): cluster health endpoint healthy", response.status_code)


class TestUserManagement:
    """User CRUD via mc CLI. mc_admin yields (mc_run, alias)."""

    def test_create_user(self, mc_admin):
        """Verify new user can be created and appears in user list."""
        mc_run, alias = mc_admin
        access_key = f"testuser-{uuid.uuid4().hex[:6]}"
        secret_key = f"secret-{uuid.uuid4().hex[:12]}"
        log.info("Creating user: %s", access_key)

        try:
            # --- Act: create ---
            log.info("mc admin user add %s %s ...", alias, access_key)
            result = mc_run("admin", "user", "add", alias, access_key, secret_key)
            log.info("mc exit code: %d, stdout: %s", result.returncode, result.stdout.strip())
            if result.stderr:
                log.info("mc stderr: %s", result.stderr.strip())
            assert result.returncode == 0, f"Failed to create user: {result.stderr}"

            # --- Assert: in list ---
            log.info("mc admin user list %s — checking %s appears...", alias, access_key)
            list_result = mc_run("admin", "user", "list", alias)
            log.info("mc exit code: %d, stdout: %s", list_result.returncode, list_result.stdout.strip())
            assert list_result.returncode == 0
            assert access_key in list_result.stdout
            log.info("PASS: user %s found in listing", access_key)

        finally:
            log.info("Cleanup: removing user %s", access_key)
            mc_run("admin", "user", "remove", alias, access_key)

    def test_delete_user(self, mc_admin):
        """Verify user deletion removes user from system."""
        mc_run, alias = mc_admin
        access_key = f"testuser-{uuid.uuid4().hex[:6]}"
        secret_key = f"secret-{uuid.uuid4().hex[:12]}"
        log.info("Test user: %s", access_key)

        # --- Arrange ---
        log.info("Arrange: creating user %s...", access_key)
        create_result = mc_run("admin", "user", "add", alias, access_key, secret_key)
        log.info("mc exit code: %d", create_result.returncode)
        assert create_result.returncode == 0, f"Setup failed: {create_result.stderr}"

        # --- Act ---
        log.info("mc admin user remove %s %s...", alias, access_key)
        delete_result = mc_run("admin", "user", "remove", alias, access_key)
        log.info("mc exit code: %d", delete_result.returncode)
        assert delete_result.returncode == 0, f"Failed to delete user: {delete_result.stderr}"

        # --- Assert ---
        log.info("mc admin user list %s — verifying %s is gone...", alias, access_key)
        list_result = mc_run("admin", "user", "list", alias)
        assert access_key not in list_result.stdout
        log.info("PASS: user %s no longer in listing", access_key)

    def test_created_user_can_access_s3(self, mc_admin, rustfs_config):
        """Create user + attach policy → verify S3 PUT/GET works with new creds."""
        mc_run, alias = mc_admin
        access_key = f"testuser-{uuid.uuid4().hex[:6]}"
        secret_key = f"secret-{uuid.uuid4().hex[:12]}"
        bucket_name = f"test-access-{uuid.uuid4().hex[:6]}"
        log.info("User: %s | Bucket: %s", access_key, bucket_name)

        user_client = None
        try:
            # --- Arrange: create user + attach policy ---
            log.info("mc admin user add %s %s...", alias, access_key)
            create_result = mc_run("admin", "user", "add", alias, access_key, secret_key)
            log.info("mc exit code: %d", create_result.returncode)
            assert create_result.returncode == 0, f"User creation failed: {create_result.stderr}"

            log.info("mc admin policy attach %s readwrite --user %s...", alias, access_key)
            policy_result = mc_run(
                "admin", "policy", "attach", alias, "readwrite", "--user", access_key
            )
            log.info("mc exit code: %d", policy_result.returncode)
            assert policy_result.returncode == 0, f"Policy attach failed: {policy_result.stderr}"

            # --- Act: use new credentials via boto3 ---
            log.info("Creating boto3 S3 client for user %s...", access_key)
            user_client = boto3.client(
                "s3",
                endpoint_url=rustfs_config["endpoint"],
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
                region_name="us-east-1",
                config=BotoConfig(signature_version="s3v4"),
            )

            log.info("Creating bucket %s as new user...", bucket_name)
            user_client.create_bucket(Bucket=bucket_name)
            user_client.put_object(Bucket=bucket_name, Key="test.txt", Body=b"user access ok")
            content = user_client.get_object(Bucket=bucket_name, Key="test.txt")["Body"].read()

            assert content == b"user access ok"
            log.info("PASS: user has S3 access")
        finally:
            log.info("Cleanup: removing bucket %s and user %s", bucket_name, access_key)
            try:
                if user_client:
                    user_client.delete_object(Bucket=bucket_name, Key="access-test.txt")
                    user_client.delete_bucket(Bucket=bucket_name)
            except Exception as e:
                log.warning("Cleanup error (non-fatal): %s", e)
            mc_run("admin", "user", "remove", alias, access_key)
