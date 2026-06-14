# RustFS Automated Test Suite

Automated test suite for [RustFS](https://github.com/rustfs/rustfs) — an S3-compatible object storage service. Covers data plane (S3 API) and management plane (admin API) testing.

## Prerequisites

| Tool | Purpose | Install |
|------|---------|---------|
| **Python 3.14+** | Test runtime | [python.org](https://www.python.org/downloads/) or `uv python install 3.14` |
| **uv** | Package manager | [docs.astral.sh/uv](https://docs.astral.sh/uv/getting-started/installation/) |
| **Docker Desktop** | Run RustFS containers & mc image | [docs.docker.com](https://docs.docker.com/get-docker/) |
| **minio/mc Image** | Management plane operations | run `docker pull minio/mc` |

## Quick Start

```bash
# 1. Clone the repository
git clone git@github.com:jan-kvapil/RFS-POC.git
cd RFS-POC

# 2. Install dependencies
uv sync

# 3. Run full test suite (starts Docker containers automatically)
uv run pytest

# 4. Run specific test categories
uv run pytest -m data_plane      # S3 API tests only
uv run pytest -m management      # Admin API tests only

# 5. Run Specific test
uv run pytest tests/data_plane/test_s3_operations.py::TestBucketOperations::test_create_and_delete_bucket
```

HTML test reports are saved to `reports/report_YYYYMMDD_HHMMSS.html` automatically.

## Configuration

Configuration is resolved in priority order: **CLI flags → environment variables → defaults**.

| Parameter | CLI Flag | Env Var | Default |
|-----------|----------|---------|---------|
| S3 endpoint | `--endpoint` | `RUSTFS_ENDPOINT` | `http://localhost:9000` |
| Console endpoint | `--console-endpoint` | `RUSTFS_CONSOLE_ENDPOINT` | `http://localhost:9001` |
| Access key | `--access-key` | `RUSTFS_ACCESS_KEY` | `rustfsadmin` |
| Secret key | `--secret-key` | `RUSTFS_SECRET_KEY` | `rustfsadmin` |
| Compose file | `--compose-file` | `RUSTFS_COMPOSE_FILE` | `artifacts/docker-compose.yml` |

Example — run against custom endpoint:
```bash
RUSTFS_ENDPOINT=http://my-rustfs:9000 uv run pytest
# or
uv run pytest --endpoint http://my-rustfs:9000
```

## Project Structure

```
├── pyproject.toml                  # Dependencies (managed by uv)
├── uv.lock                        # Lockfile (auto-generated)
├── pytest.ini                     # Pytest configuration
├── artifacts/
│   └── docker-compose.yml         # RustFS service definition
├── reports/                       # Timestamped HTML test reports
├── helpers/
│   └── docker_helper.py           # Docker lifecycle management
└── tests/
    ├── conftest.py                # Shared fixtures and config
    ├── data_plane/
    │   └── test_s3_operations.py  # S3 API tests (boto3)
    └── management/
        └── test_admin_api.py      # Admin API tests (requests + mc)
```

## Test Inventory

### Data Plane (7 tests)

| Test | What it validates |
|------|-------------------|
| `test_create_and_delete_bucket` | Bucket lifecycle — create, verify exists, delete, verify gone |
| `test_put_and_get_object` | Object content integrity — PUT body matches GET body exactly |
| `test_put_object_with_metadata` | Custom metadata stored and returned correctly |
| `test_list_objects` | list_objects_v2 returns all PUT objects with correct keys |
| `test_delete_object` | Deleted object returns NoSuchKey error on GET |
| `test_get_nonexistent_object` | Non-existent key returns proper NoSuchKey error |
| `test_overwrite_object` | PUT to existing key replaces content |

### Management Plane (5 tests)

| Test | What it validates |
|------|-------------------|
| `test_health_live` | Liveness probe returns HTTP 200 |
| `test_health_cluster` | Cluster health endpoint returns HTTP 200 |
| `test_create_user` | Admin can create user, user appears in listing |
| `test_delete_user` | Admin can delete user, user removed from listing |
| `test_created_user_can_access_s3` | Created user with readwrite policy can PUT/GET objects |

## Assumptions

- RustFS exposes MinIO-compatible S3 API on port 9000
- RustFS exposes MinIO-compatible admin API (used by `mc`)
- Health endpoints `/minio/health/live` and `/minio/health/cluster` are unauthenticated
- Docker Desktop is running and docker compose v2 is available
- `minio/mc` Docker image is available locally for management tests
- Tests run on single-node RustFS (as defined in docker-compose.yml)

## Limitations

- **No parallel test execution** — tests run sequentially to avoid S3 state conflicts
- **No TLS testing** — docker-compose uses HTTP, not HTTPS
- **No performance/load testing** — focus is on functional correctness
- **No versioned bucket testing** — requires additional configuration

## Manual Cleanup

If tests crash and leave stale containers:

```bash
docker compose -f artifacts/docker-compose.yml down -v --remove-orphans
```

## What Would Be Tested Next

1. **Multipart uploads** — large file upload/download integrity
2. **Pre-signed URLs** — generate and validate time-limited access URLs
3. **Bucket policies** — anonymous access, IP restrictions
4. **Object versioning** — version-enabled bucket behavior
5. **SSE (Server-Side Encryption)** — encryption at rest
6. **Concurrent access** — parallel PUT/GET to same key
7. **GUI test** — Playwright login flow for web console (port 9001)
8. **CI/CD integration** — GitHub Actions workflow with Docker service
