"""Shared pytest fixtures and configuration for RustFS test suite."""

import os
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import boto3
import pytest
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

# --- Defaults matching docker-compose.yml ---

_DEFAULTS = {
    "endpoint": "http://localhost:9000",
    "console_endpoint": "http://localhost:9001",
    "access_key": "rustfsadmin",
    "secret_key": "rustfsadmin",
    "compose_file": str(Path(__file__).parent.parent / "artifacts" / "docker-compose.yml"),
}


def _resolve(request, cli_flag: str, env_var: str, default: str) -> str:
    """Resolve config: CLI option → env var → default."""
    cli_value = request.config.getoption(cli_flag, default=None)
    if cli_value is not None:
        return cli_value
    return os.environ.get(env_var, default)


# --- pytest hooks ---


def pytest_addoption(parser):
    """Register CLI options for RustFS configuration."""
    group = parser.getgroup("rustfs", "RustFS test configuration")
    group.addoption("--endpoint", default=None, help="S3 endpoint URL")
    group.addoption("--console-endpoint", default=None, help="Console endpoint URL")
    group.addoption("--access-key", default=None, help="Access key")
    group.addoption("--secret-key", default=None, help="Secret key")
    group.addoption("--compose-file", default=None, help="Path to docker-compose.yml")


def pytest_configure(config):
    """Set timestamped HTML report path."""
    if config.option.htmlpath is None:
        reports_dir = Path(__file__).parent.parent / "reports"
        reports_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        config.option.htmlpath = str(reports_dir / f"report_{timestamp}.html")
        config.option.self_contained_html = True


# --- Pre-flight checks ---


@pytest.fixture(scope="session", autouse=True)
def _check_docker():
    """Fail fast if Docker or Docker Compose v2 is missing."""
    for cmd, hint in [
        (["docker", "--version"], "Install Docker Desktop: https://docs.docker.com/get-docker/"),
        (["docker", "compose", "version"], "Docker Compose v2 required (included in Docker Desktop)"),
    ]:
        try:
            subprocess.run(cmd, capture_output=True, check=True, timeout=10)
        except (FileNotFoundError, subprocess.CalledProcessError):
            pytest.exit(f"Missing: {' '.join(cmd)}\n{hint}")


# --- Docker lifecycle ---


@pytest.fixture(scope="session")
def rustfs_config(request):
    """Resolve all RustFS config values (CLI → env → default)."""
    return {
        "endpoint": _resolve(request, "--endpoint", "RUSTFS_ENDPOINT", _DEFAULTS["endpoint"]),
        "console_endpoint": _resolve(
            request, "--console-endpoint", "RUSTFS_CONSOLE_ENDPOINT", _DEFAULTS["console_endpoint"]
        ),
        "access_key": _resolve(request, "--access-key", "RUSTFS_ACCESS_KEY", _DEFAULTS["access_key"]),
        "secret_key": _resolve(request, "--secret-key", "RUSTFS_SECRET_KEY", _DEFAULTS["secret_key"]),
        "compose_file": _resolve(
            request, "--compose-file", "RUSTFS_COMPOSE_FILE", _DEFAULTS["compose_file"]
        ),
    }


@pytest.fixture(scope="session", autouse=True)
def rustfs_service(rustfs_config):
    """Start RustFS, wait for health, yield project_name, stop on teardown."""
    from helpers.docker_helper import start_service, stop_service, wait_for_health

    compose_path = Path(rustfs_config["compose_file"])
    endpoint = rustfs_config["endpoint"]

    try:
        project_name = start_service(compose_path)
    except RuntimeError as exc:
        pytest.exit(f"RustFS failed to start:\n{exc}")

    try:
        wait_for_health(endpoint, timeout=120)
    except (TimeoutError, RuntimeError) as exc:
        stop_service(compose_path, project_name)
        pytest.exit(f"RustFS failed to start:\n{exc}")

    yield project_name

    stop_service(compose_path, project_name)


# --- S3 client ---


@pytest.fixture(scope="session")
def s3_client(rustfs_config, rustfs_service):
    """boto3 S3 client configured for RustFS."""
    return boto3.client(
        "s3",
        endpoint_url=rustfs_config["endpoint"],
        aws_access_key_id=rustfs_config["access_key"],
        aws_secret_access_key=rustfs_config["secret_key"],
        region_name="us-east-1",
        config=BotoConfig(signature_version="s3v4"),
    )


# --- Test bucket ---


@pytest.fixture(scope="session")
def test_bucket(s3_client):
    """Create unique test bucket, clean up after session."""
    bucket_name = f"test-{uuid.uuid4().hex[:8]}"
    s3_client.create_bucket(Bucket=bucket_name)

    yield bucket_name

    _empty_bucket(s3_client, bucket_name)
    s3_client.delete_bucket(Bucket=bucket_name)


def _empty_bucket(s3_client, bucket_name: str) -> None:
    """Delete all objects in bucket."""
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket_name):
        objects = page.get("Contents", [])
        if objects:
            s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": obj["Key"]} for obj in objects]},
            )


# --- mc CLI helper (via minio/mc Docker image) ---


@pytest.fixture(scope="session")
def mc_admin(rustfs_config, rustfs_service):
    """Yield (mc_run, alias). Caller places alias in correct arg position."""
    project_name = rustfs_service
    alias = "rustfs"
    network = f"{project_name}_rustfs-net"

    # Check minio/mc image exists locally.
    result = subprocess.run(["docker", "image", "inspect", "minio/mc"], capture_output=True)
    if result.returncode != 0:
        pytest.skip("minio/mc image not found. Pull: docker pull minio/mc")

    access_key = rustfs_config["access_key"]
    secret_key = rustfs_config["secret_key"]
    mc_host_env = f"MC_HOST_{alias}=http://{access_key}:{secret_key}@rustfs:9000"

    def mc_run(*args: str) -> subprocess.CompletedProcess:
        """Run mc inside Docker. Caller must include alias in args."""
        cmd = [
            "docker", "run", "--rm",
            "--network", network,
            "--env", mc_host_env,
            "minio/mc", "--insecure",
            *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    yield mc_run, alias
