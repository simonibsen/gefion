"""
Service health checks for Gefion infrastructure.

Provides functions to check if required services (PostgreSQL, Tempo, etc.)
are running and accessible, with helpful error messages and suggestions.
"""
import os
from typing import Dict, Any


def check_postgres_health(url: str | None = None, timeout: int = 2) -> Dict[str, Any]:
    """
    Check if PostgreSQL is running and accessible.

    Args:
        url: Database URL (defaults to DATABASE_URL env var)
        timeout: Connection timeout in seconds

    Returns:
        Dict with:
            - running: bool - whether service is accessible
            - message: str - success/error message
            - suggestion: str - how to fix if not running (optional)
            - error_type: str - category of error (optional)
    """
    try:
        import psycopg

        db_url = url or os.getenv(
            "DATABASE_URL",
            "postgresql://gefion:gefionpass@localhost:6432/gefion"
        )

        with psycopg.connect(db_url, connect_timeout=timeout) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                version = cur.fetchone()[0]

        return {
            "running": True,
            "message": f"PostgreSQL is healthy",
            "version": version.split(',')[0]  # Just PostgreSQL version part
        }

    except psycopg.OperationalError as e:
        error_msg = str(e)

        # Categorize the error for better suggestions
        if "Connection refused" in error_msg:
            return {
                "running": False,
                "message": "PostgreSQL is not running",
                "error": "Connection refused",
                "error_type": "not_running",
                "suggestion": "Start PostgreSQL:\n  docker compose up -d postgres\n\nOr check if it's running:\n  docker compose ps postgres"
            }
        elif "timeout" in error_msg.lower():
            return {
                "running": False,
                "message": "PostgreSQL connection timed out",
                "error": "Connection timeout",
                "error_type": "timeout",
                "suggestion": "PostgreSQL may be starting up or overloaded.\nWait a moment and try again, or check logs:\n  docker compose logs postgres"
            }
        elif "authentication failed" in error_msg.lower():
            return {
                "running": False,
                "message": "PostgreSQL authentication failed",
                "error": "Invalid credentials",
                "error_type": "auth_failed",
                "suggestion": "Check DATABASE_URL credentials.\nDefault: postgresql://gefion:gefionpass@localhost:6432/gefion"
            }
        else:
            return {
                "running": False,
                "message": "PostgreSQL connection failed",
                "error": error_msg[:200],  # Truncate long errors
                "error_type": "connection_error",
                "suggestion": "Check if PostgreSQL is running:\n  docker compose ps postgres\n\nView logs:\n  docker compose logs postgres"
            }

    except ImportError:
        return {
            "running": False,
            "message": "psycopg library not installed",
            "error": "Missing dependency",
            "error_type": "missing_dependency",
            "suggestion": "Install psycopg:\n  pip install psycopg[binary]"
        }

    except Exception as e:
        return {
            "running": False,
            "message": "Unexpected error checking PostgreSQL",
            "error": str(e)[:200],
            "error_type": "unknown",
            "suggestion": "Check database configuration and logs:\n  docker compose logs postgres"
        }


def check_tempo_health(endpoint: str | None = None, timeout: int = 2) -> Dict[str, Any]:
    """
    Check if Tempo (OpenTelemetry tracing backend) is running.

    Uses Docker container detection first, then HTTP health check.
    This avoids false positives from SSH tunnels or other services.

    Args:
        endpoint: Tempo endpoint (defaults to http://localhost:3200)
        timeout: Request timeout in seconds

    Returns:
        Dict with health status and suggestions
    """
    import subprocess

    # First check if tempo container is running via Docker
    try:
        result = subprocess.run(
            ["docker", "ps", "--filter", "name=tempo", "--format", "{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=timeout
        )

        container_running = result.returncode == 0 and "tempo" in result.stdout.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Docker not available, fall through to HTTP check
        container_running = None

    # If Docker says no tempo container, report not running
    if container_running is False:
        return {
            "running": False,
            "message": "Tempo container is not running",
            "error_type": "not_running",
            "suggestion": "Tempo is optional for most operations.\n\nTo enable tracing:\n  docker compose -f docker/tempo/docker-compose.tempo.yml up -d\n\nTo disable tracing warnings:\n  export OTEL_ENABLED=false"
        }

    # Verify via HTTP endpoint (container running or Docker unavailable)
    try:
        import requests

        tempo_url = endpoint or "http://localhost:3200"
        health_url = f"{tempo_url}/api/echo"

        response = requests.get(health_url, timeout=timeout)

        if response.status_code == 200:
            return {
                "running": True,
                "message": "Tempo is healthy",
                "endpoint": tempo_url
            }
        else:
            return {
                "running": False,
                "message": f"Tempo returned status {response.status_code}",
                "error_type": "unhealthy",
                "suggestion": "Tempo may be running but unhealthy.\nCheck logs:\n  docker compose -f docker/tempo/docker-compose.tempo.yml logs tempo"
            }

    except requests.exceptions.ConnectionError:
        return {
            "running": False,
            "message": "Tempo is not running or not accessible",
            "error": "Connection refused",
            "error_type": "not_running",
            "suggestion": "Tempo is optional for most operations.\n\nTo enable tracing:\n  docker compose -f docker/tempo/docker-compose.tempo.yml up -d\n\nTo disable tracing warnings:\n  export OTEL_ENABLED=false"
        }

    except requests.exceptions.Timeout:
        return {
            "running": False,
            "message": "Tempo connection timed out",
            "error_type": "timeout",
            "suggestion": "Tempo may be starting up.\nCheck status:\n  docker compose -f docker/tempo/docker-compose.tempo.yml ps"
        }

    except ImportError:
        return {
            "running": False,
            "message": "requests library not installed",
            "error_type": "missing_dependency",
            "suggestion": "Tempo health checks require requests:\n  pip install requests"
        }

    except Exception as e:
        return {
            "running": False,
            "message": "Unexpected error checking Tempo",
            "error": str(e)[:200],
            "error_type": "unknown"
        }


def check_docker_services() -> Dict[str, Any]:
    """
    Check if Docker and docker-compose are available.

    Returns:
        Dict with Docker availability status
    """
    import subprocess

    try:
        # Check if docker is available
        result = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            timeout=2
        )

        if result.returncode != 0:
            return {
                "running": False,
                "message": "Docker is not available",
                "error_type": "not_installed",
                "suggestion": "Install Docker:\n  https://docs.docker.com/get-docker/"
            }

        # Check if docker compose is available
        compose_result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=2
        )

        if compose_result.returncode != 0:
            return {
                "running": False,
                "message": "Docker Compose is not available",
                "error_type": "compose_not_installed",
                "suggestion": "Docker Compose is required.\nUpdate Docker to get Compose v2."
            }

        return {
            "running": True,
            "message": "Docker and Docker Compose are available",
            "docker_version": result.stdout.strip(),
            "compose_version": compose_result.stdout.strip()
        }

    except FileNotFoundError:
        return {
            "running": False,
            "message": "Docker is not installed",
            "error_type": "not_installed",
            "suggestion": "Install Docker:\n  https://docs.docker.com/get-docker/"
        }

    except subprocess.TimeoutExpired:
        return {
            "running": False,
            "message": "Docker command timed out",
            "error_type": "timeout",
            "suggestion": "Docker may be unresponsive.\nRestart Docker Desktop or the Docker daemon."
        }

    except Exception as e:
        return {
            "running": False,
            "message": "Error checking Docker",
            "error": str(e)[:200],
            "error_type": "unknown"
        }


def check_grafana_health(endpoint: str | None = None, timeout: int = 2) -> Dict[str, Any]:
    """
    Check if Grafana is running.

    Args:
        endpoint: Grafana endpoint (defaults to http://localhost:3000)
        timeout: Request timeout in seconds

    Returns:
        Dict with health status
    """
    try:
        import requests

        grafana_url = endpoint or "http://localhost:3000"
        health_url = f"{grafana_url}/api/health"

        response = requests.get(health_url, timeout=timeout)

        if response.status_code == 200:
            return {
                "running": True,
                "message": "Grafana is healthy",
                "endpoint": grafana_url
            }
        else:
            return {
                "running": False,
                "message": f"Grafana returned status {response.status_code}",
                "error_type": "unhealthy",
                "suggestion": "Grafana may be running but unhealthy.\nCheck logs:\n  docker compose -f docker/tempo/docker-compose.tempo.yml logs grafana"
            }

    except requests.exceptions.ConnectionError:
        return {
            "running": False,
            "message": "Grafana is not running",
            "error": "Connection refused",
            "error_type": "not_running",
            "suggestion": "Grafana is optional (for trace visualization).\n\nTo start:\n  docker compose -f docker/tempo/docker-compose.tempo.yml up -d grafana"
        }

    except requests.exceptions.Timeout:
        return {
            "running": False,
            "message": "Grafana connection timed out",
            "error_type": "timeout"
        }

    except Exception as e:
        return {
            "running": False,
            "message": "Error checking Grafana",
            "error": str(e)[:200],
            "error_type": "unknown"
        }


def check_all_services() -> Dict[str, Dict[str, Any]]:
    """
    Check health of all Gefion services.

    Returns:
        Dict mapping service name to health status
    """
    return {
        "docker": check_docker_services(),
        "postgres": check_postgres_health(),
        "tempo": check_tempo_health(),
        "grafana": check_grafana_health(),
    }


def format_health_report(health_status: Dict[str, Dict[str, Any]]) -> str:
    """
    Format health check results into a readable report.

    Args:
        health_status: Output from check_all_services()

    Returns:
        Formatted string report
    """
    lines = ["=== Service Health Check ===\n"]

    for service, status in health_status.items():
        running = status.get("running", False)
        status_icon = "✓" if running else "✗"

        lines.append(f"{status_icon} {service.upper()}: {status['message']}")

        if not running and "suggestion" in status:
            lines.append(f"   → {status['suggestion']}\n")
        elif running and "version" in status:
            lines.append(f"   Version: {status['version']}\n")
        else:
            lines.append("")

    return "\n".join(lines)
