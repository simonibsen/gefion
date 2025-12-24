"""
Tests for MCP server health check integration.

Following TDD: Write tests first, then implement.
"""
import pytest
from unittest.mock import patch, MagicMock
import time


# Import will be from the server module once implemented
# For now, we're defining expected behavior


class TestHealthCheckCache:
    """Tests for health check caching logic."""

    def test_cache_returns_cached_result_within_ttl(self):
        """Test that cached health results are returned within TTL."""
        # This test defines expected behavior:
        # - First check: calls actual health check
        # - Second check within TTL: returns cached result
        # - After TTL expires: calls actual health check again

        from server import HealthCheckCache

        cache = HealthCheckCache(ttl_seconds=2)

        # Mock health check function
        mock_check = MagicMock(return_value={"running": True, "message": "Healthy"})

        # First call - should invoke health check
        result1 = cache.get_or_check("postgres", mock_check)
        assert result1 == {"running": True, "message": "Healthy"}
        assert mock_check.call_count == 1

        # Second call within TTL - should return cached
        result2 = cache.get_or_check("postgres", mock_check)
        assert result2 == {"running": True, "message": "Healthy"}
        assert mock_check.call_count == 1  # Still 1, not called again

        # Wait for TTL to expire
        time.sleep(2.1)

        # Third call after TTL - should invoke health check again
        result3 = cache.get_or_check("postgres", mock_check)
        assert result3 == {"running": True, "message": "Healthy"}
        assert mock_check.call_count == 2  # Called again

    def test_cache_different_services_separately(self):
        """Test that different services are cached separately."""
        from server import HealthCheckCache

        cache = HealthCheckCache(ttl_seconds=60)

        mock_postgres = MagicMock(return_value={"running": True, "service": "postgres"})
        mock_tempo = MagicMock(return_value={"running": False, "service": "tempo"})

        # Check different services
        result_pg = cache.get_or_check("postgres", mock_postgres)
        result_tempo = cache.get_or_check("tempo", mock_tempo)

        assert result_pg == {"running": True, "service": "postgres"}
        assert result_tempo == {"running": False, "service": "tempo"}
        assert mock_postgres.call_count == 1
        assert mock_tempo.call_count == 1

    def test_cache_invalidation(self):
        """Test manual cache invalidation."""
        from server import HealthCheckCache

        cache = HealthCheckCache(ttl_seconds=60)

        mock_check = MagicMock(return_value={"running": True})

        # First call
        cache.get_or_check("postgres", mock_check)
        assert mock_check.call_count == 1

        # Invalidate cache
        cache.invalidate("postgres")

        # Next call should invoke check again despite TTL
        cache.get_or_check("postgres", mock_check)
        assert mock_check.call_count == 2


class TestMCPHealthIntegration:
    """Tests for MCP tool health check integration."""

    def test_tool_checks_required_service_before_execution(self):
        """Test that MCP tools check required services before running."""
        from server import check_service_health

        # Mock postgres check failing
        with patch('server.health.check_postgres_health', return_value={
            "running": False,
            "message": "PostgreSQL is not running",
            "suggestion": "Start with: docker compose up -d postgres"
        }):
            result = check_service_health("postgres")

            assert result["running"] is False
            assert "PostgreSQL is not running" in result["message"]
            assert "docker compose up -d postgres" in result["suggestion"]

    def test_tool_provides_helpful_error_when_service_down(self):
        """Test that tools provide helpful docker-compose suggestions."""
        from server import format_service_error

        postgres_down = {
            "running": False,
            "message": "PostgreSQL is not running",
            "error_type": "not_running",
            "suggestion": "Start PostgreSQL:\n  docker compose up -d postgres"
        }

        error_msg = format_service_error("postgres", postgres_down)

        assert "PostgreSQL is not running" in error_msg
        assert "docker compose up -d postgres" in error_msg

    def test_tempo_error_includes_compose_file_location(self):
        """Test that Tempo errors reference the correct docker-compose location."""
        from server import format_service_error

        tempo_down = {
            "running": False,
            "message": "Tempo is not running",
            "error_type": "not_running"
        }

        error_msg = format_service_error("tempo", tempo_down)

        # Should reference the tempo-specific docker-compose location
        assert "docker/tempo" in error_msg or "docker-compose.tempo.yml" in error_msg

    def test_health_check_overhead_is_minimal(self):
        """Test that health check caching keeps overhead low."""
        from server import HealthCheckCache

        cache = HealthCheckCache(ttl_seconds=60)

        # Simulate a slow health check (50ms)
        slow_check = MagicMock(side_effect=lambda: (time.sleep(0.05), {"running": True})[1])

        # First call - takes ~50ms
        start = time.time()
        cache.get_or_check("postgres", slow_check)
        first_duration = time.time() - start
        assert first_duration >= 0.05  # At least 50ms

        # Second call - should be cached, much faster
        start = time.time()
        cache.get_or_check("postgres", slow_check)
        cached_duration = time.time() - start
        assert cached_duration < 0.01  # Less than 10ms (cached)

        # Verify overhead is minimal
        assert cached_duration < first_duration / 5  # At least 5x faster when cached


class TestHealthCheckTool:
    """Tests for health_check MCP tool."""

    def test_health_check_tool_returns_all_services(self):
        """Test that health_check tool returns status for all services."""
        # This test defines expected behavior for the health_check MCP tool
        # Expected: Returns Docker, PostgreSQL, and Tempo health status
        pass  # Will implement after adding tool

    def test_health_check_tool_with_service_filter(self):
        """Test health_check with specific service filter."""
        # Expected: Can check just postgres, tempo, or docker individually
        pass

    def test_health_check_tool_returns_json_format(self):
        """Test that health_check returns properly formatted JSON."""
        # Expected: Returns {"status": "ok", "services": {...}}
        pass


class TestDockerStatusTool:
    """Tests for docker_status MCP tool."""

    def test_docker_status_lists_running_containers(self):
        """Test that docker_status shows running containers."""
        # Expected: Lists containers from docker compose ps
        pass

    def test_docker_status_shows_health_status(self):
        """Test that container health status is included."""
        # Expected: Shows healthy/unhealthy for containers with healthchecks
        pass

    def test_docker_status_handles_no_containers(self):
        """Test docker_status when no containers are running."""
        # Expected: Returns empty list or helpful message
        pass

    def test_docker_status_includes_port_mappings(self):
        """Test that port mappings are shown."""
        # Expected: Shows port forwards like "6432->5432/tcp"
        pass
