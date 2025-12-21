# Multi-stage build for g2 MCP server
# Includes g2 CLI + ML dependencies + MCP server

FROM python:3.11-slim as base

# Install system dependencies
RUN apt-get update && apt-get install -y \
    postgresql-client \
    git \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# ============================================================================
# Stage 1: Install g2 CLI with ML dependencies
# ============================================================================

# Copy g2 project files
COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY sql/ ./sql/

# Install g2 with ML dependencies
RUN pip install --no-cache-dir -e ".[ml_extended]"

# ============================================================================
# Stage 2: Install MCP server
# ============================================================================

# Copy MCP server files
COPY mcp-server/ ./mcp-server/

# Install MCP server dependencies
RUN pip install --no-cache-dir -r mcp-server/requirements.txt

# ============================================================================
# Configuration
# ============================================================================

# Environment variables (override in docker-compose.yml or at runtime)
ENV DATABASE_URL="postgresql://g2:g2pass@postgres:5432/g2"
ENV ALPHAVANTAGE_API_KEY=""

# Health check - verify g2 is installed
RUN g2 --version

# Default command runs MCP server
CMD ["python", "mcp-server/server.py"]

# ============================================================================
# Usage Examples
# ============================================================================

# Build:
#   docker build -t g2-mcp-server .

# Run MCP server (for Claude Desktop):
#   docker run --rm -i --env-file .env g2-mcp-server

# Run g2 CLI directly:
#   docker run --rm --env-file .env g2-mcp-server g2 ml dataset-build --help

# Run with GPU support:
#   docker run --rm -i --gpus all --env-file .env g2-mcp-server
