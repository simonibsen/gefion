"""
Tests for service health check CLI commands.

Following TDD: Write tests first, then implement.
"""
import json
from unittest.mock import patch, MagicMock
from typer.testing import CliRunner

from g2.cli import app


runner = CliRunner()


def test_health_check_all_services_healthy():
    """Test health check when all services are running."""
    mock_health_status = {
        "docker": {
            "running": True,
            "message": "Docker and Docker Compose are available",
            "docker_version": "Docker version 24.0.0",
            "compose_version": "Docker Compose version v2.20.0"
        },
        "postgres": {
            "running": True,
            "message": "PostgreSQL is healthy",
            "version": "PostgreSQL 16.0"
        },
        "tempo": {
            "running": True,
            "message": "Tempo is healthy",
            "endpoint": "http://localhost:3200"
        }
    }

    with patch('g2.cli.health.check_all_services', return_value=mock_health_status):
        result = runner.invoke(app, ["health"])

    assert result.exit_code == 0
    assert "✅" in result.stdout
    assert "DOCKER" in result.stdout
    assert "POSTGRES" in result.stdout
    assert "TEMPO" in result.stdout


def test_health_check_postgres_not_running():
    """Test health check when PostgreSQL is not running."""
    mock_health_status = {
        "docker": {
            "running": True,
            "message": "Docker and Docker Compose are available"
        },
        "postgres": {
            "running": False,
            "message": "PostgreSQL is not running",
            "error": "Connection refused",
            "error_type": "not_running",
            "suggestion": "Start PostgreSQL:\n  docker compose up -d postgres\n\nOr check if it's running:\n  docker compose ps postgres"
        },
        "tempo": {
            "running": False,
            "message": "Tempo is not running or not accessible",
            "error_type": "not_running",
            "suggestion": "Tempo is optional for most operations.\n\nTo enable tracing:\n  docker compose -f docker/tempo/docker-compose.tempo.yml up -d\n\nTo disable tracing warnings:\n  export OTEL_ENABLED=false"
        }
    }

    with patch('g2.cli.health.check_all_services', return_value=mock_health_status):
        result = runner.invoke(app, ["health"])

    # Should still exit successfully but show errors
    assert result.exit_code == 0
    assert "❌" in result.stdout
    assert "POSTGRES" in result.stdout
    assert "Connection refused" in result.stdout or "not running" in result.stdout.lower()
    assert "docker compose up -d postgres" in result.stdout


def test_health_check_json_output():
    """Test health check with JSON output."""
    mock_health_status = {
        "docker": {
            "running": True,
            "message": "Docker and Docker Compose are available"
        },
        "postgres": {
            "running": True,
            "message": "PostgreSQL is healthy",
            "version": "PostgreSQL 16.0"
        },
        "tempo": {
            "running": True,
            "message": "Tempo is healthy"
        }
    }

    with patch('g2.cli.health.check_all_services', return_value=mock_health_status):
        result = runner.invoke(app, ["health", "--json"])

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["status"] == "ok"
    assert "services" in output
    assert "docker" in output["services"]
    assert "postgres" in output["services"]
    assert "tempo" in output["services"]
    assert output["services"]["postgres"]["running"] is True


def test_health_check_specific_service():
    """Test health check for a specific service."""
    mock_health_status = {
        "running": True,
        "message": "PostgreSQL is healthy",
        "version": "PostgreSQL 16.0"
    }

    with patch('g2.cli.health.check_postgres_health', return_value=mock_health_status):
        result = runner.invoke(app, ["health", "--service", "postgres"])

    assert result.exit_code == 0
    assert "✅" in result.stdout
    assert "POSTGRES" in result.stdout
    assert "PostgreSQL 16.0" in result.stdout


def test_health_check_service_with_suggestion():
    """Test that suggestions are displayed for failed services."""
    mock_health_status = {
        "running": False,
        "message": "PostgreSQL is not running",
        "error": "Connection refused",
        "error_type": "not_running",
        "suggestion": "Start PostgreSQL:\n  docker compose up -d postgres"
    }

    with patch('g2.cli.health.check_postgres_health', return_value=mock_health_status):
        result = runner.invoke(app, ["health", "--service", "postgres"])

    assert result.exit_code == 0  # Health check itself doesn't fail
    assert "❌" in result.stdout
    assert "Start PostgreSQL" in result.stdout
    assert "docker compose up -d postgres" in result.stdout
