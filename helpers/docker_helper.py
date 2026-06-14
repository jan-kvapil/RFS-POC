"""Docker Compose lifecycle helper for RustFS test suite."""

import logging
import subprocess
import time
import uuid
from pathlib import Path

import requests

log = logging.getLogger(__name__)

DEFAULT_COMPOSE_PATH = Path(__file__).parent.parent / "artifacts" / "docker-compose.yml"


def _make_project_name() -> str:
    """Generate unique docker compose project name."""
    return f"rustfs-test-{uuid.uuid4().hex[:8]}"


def _base_cmd(compose_path: Path, project_name: str) -> list[str]:
    """Build base docker compose command."""
    return ["docker", "compose", "-p", project_name, "-f", str(compose_path)]


def start_service(
    compose_path: Path = DEFAULT_COMPOSE_PATH,
    project_name: str | None = None,
) -> str:
    """Start RustFS containers. Returns project_name."""
    if project_name is None:
        project_name = _make_project_name()

    print(f"\n  Starting RustFS containers (project={project_name})...", flush=True)
    log.info("Starting project=%s from %s", project_name, compose_path)
    result = subprocess.run(
        [*_base_cmd(compose_path, project_name), "up", "-d", "--remove-orphans"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker compose up failed (exit {result.returncode}):\n"
            f"STDOUT: {result.stdout}\nSTDERR: {result.stderr}"
        )
    print(f"  ✓ Containers started (project={project_name})", flush=True)
    return project_name


def stop_service(
    compose_path: Path = DEFAULT_COMPOSE_PATH,
    project_name: str | None = None,
) -> None:
    """Stop RustFS containers and remove volumes. Non-fatal on error."""
    if project_name is None:
        log.warning("stop_service called without project_name — skipping.")
        return

    log.info("Stopping project=%s", project_name)
    result = subprocess.run(
        [*_base_cmd(compose_path, project_name), "down", "-v", "--remove-orphans"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.warning("docker compose down exit %d: %s", result.returncode, result.stderr)


def wait_for_health(
    endpoint: str,
    timeout: int = 120,
    interval: int = 2,
) -> bool:
    """Poll /health until 200 or timeout. Prints progress."""
    health_url = f"{endpoint}/health"
    deadline = time.time() + timeout
    start = time.time()
    last_report = start

    print(f"\n  Waiting for RustFS at {health_url} (timeout={timeout}s)...", flush=True)

    while time.time() < deadline:
        elapsed = time.time() - start
        try:
            response = requests.get(health_url, timeout=5)
            if response.status_code == 200:
                print(f"  ✓ RustFS healthy after {elapsed:.1f}s", flush=True)
                return True
        except (requests.ConnectionError, requests.Timeout):
            pass

        if time.time() - last_report >= 10:
            print(f"  ... still waiting ({elapsed:.0f}s elapsed)", flush=True)
            last_report = time.time()

        time.sleep(interval)

    raise TimeoutError(f"RustFS not healthy at {health_url} within {timeout}s")


def cleanup(compose_path: Path = DEFAULT_COMPOSE_PATH) -> None:
    """Force cleanup containers and volumes. For manual recovery."""
    log.info("Force cleanup.")
    subprocess.run(
        ["docker", "compose", "-f", str(compose_path), "down", "-v", "--remove-orphans"],
        capture_output=True,
        text=True,
    )
