# MCP Server Production Deployment

Guide for deploying the g2 MCP server in production with role-based access control.

## Roles

The MCP server supports two roles:

| Role | Description | Default |
|------|-------------|---------|
| `operator` | Data operations and monitoring only | Yes (safer) |
| `developer` | Full access including dev tools | No |

## Configuration

Set the role via environment variable:

```bash
# Production (default - restricted access)
G2_MCP_ROLE=operator

# Development (full access)
G2_MCP_ROLE=developer
```

## Tool Access by Role

### Available to Both Roles

- `data_update` - Update prices and features
- `ml_dataset_build`, `ml_train`, `ml_predict`, `ml_eval` - ML workflow
- `ml_train_classifier`, `ml_predict_classifier` - Classifier workflow
- `query_predictions`, `query_model_performance` - Query results
- `query_database` - SQL queries (SELECT only, auto-limited to 1000 rows)
- `features_list`, `cross_sectional_compute` - Feature management
- `span_check`, `trace_search`, `trace_detail`, `trace_compare` - Observability
- `system_status`, `health_check`, `docker_status` - Infrastructure
- `strategy_list`, `strategy_configs`, `strategy_create_config` - Strategy management
- `get_role_info` - Get current role and guidelines

### Blocked for Operator Role

- `dev_status` - Exposes internal roadmap and development docs

## SQL Safety

The `query_database` tool enforces read-only access for all roles:

- Only `SELECT` and `WITH` (CTEs) queries allowed
- Blocks: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`
- Auto-adds `LIMIT 1000` if not specified

## Operator Role Guidelines

When running as operator, the MCP server returns behavioral guidelines via `get_role_info`:

1. Focus on data operations, ML training, and monitoring
2. Do not suggest code changes or modifications
3. Do not attempt to read or modify source files
4. Use MCP tools for all operations
5. SQL queries are read-only (SELECT only)

## Deployment Best Practices

### 1. Use Operator Role by Default

The server defaults to operator role. No action needed for production.

```bash
# This starts in operator mode (default)
python mcp-server/server.py
```

### 2. Isolate from Source Code

Run the MCP server in a directory without access to source code:

```bash
# Create isolated deployment directory
mkdir -p /opt/g2-mcp
cp mcp-server/server.py /opt/g2-mcp/

# Run from isolated directory
cd /opt/g2-mcp
python server.py
```

### 3. Configure Claude Desktop for Production

Example `claude_desktop_config.json` for production:

```json
{
  "mcpServers": {
    "g2-prod": {
      "command": "python",
      "args": ["/opt/g2-mcp/server.py"],
      "env": {
        "G2_MCP_ROLE": "operator",
        "DATABASE_URL": "postgresql://g2:password@db.example.com:5432/g2"
      }
    }
  }
}
```

### 4. For Development

When developing, explicitly enable developer role:

```json
{
  "mcpServers": {
    "g2-dev": {
      "command": "python",
      "args": ["/path/to/g2/mcp-server/server.py"],
      "env": {
        "G2_MCP_ROLE": "developer",
        "DATABASE_URL": "postgresql://g2:g2pass@localhost:5432/g2"
      }
    }
  }
}
```

## Verifying Role

Use the `get_role_info` tool to verify current role and guidelines:

```
Tool: get_role_info
Arguments: {}

Response:
{
  "success": true,
  "role": "operator",
  "description": "Data operations and monitoring only",
  "guidelines": [
    "Focus on data operations, ML training, and monitoring",
    "Do not suggest code changes or modifications",
    "Do not attempt to read or modify source files",
    "Use MCP tools for all operations",
    "SQL queries are read-only (SELECT only)"
  ],
  "blocked_tools": ["dev_status"]
}
```

## Troubleshooting

### "Access denied" Error

If you see `Access denied: 'dev_status' is not available in operator role`:

- This is expected behavior in operator mode
- Switch to developer role if you need this tool: `G2_MCP_ROLE=developer`

### Role Not Taking Effect

The role is read at server startup. Restart the MCP server after changing `G2_MCP_ROLE`.

## Limitations and Future Directions

### Current Limitation: Advisory Role Enforcement

The MCP role system is **advisory only**. It provides guidelines to the LLM about expected behavior, but does not enforce restrictions on Claude Code's core tools.

**The gap:**
- MCP server returns role guidelines via `get_role_info`
- Claude Code's core tools (Read, Edit, Write, Bash, Glob, Grep) remain available regardless of MCP role
- An LLM in "operator" mode can still edit source files using Claude Code's built-in tools

**Why this matters:**
- In production, you may want to guarantee that an operator cannot modify code
- Currently, role compliance depends on the LLM following guidelines, not technical enforcement

### Potential Solutions to Explore

1. **Claude Code Hooks** - Pre-tool hooks could check the MCP role and block file operations:
   ```bash
   # .claude/hooks/pre-tool.sh
   if [[ "$TOOL_NAME" =~ ^(Edit|Write)$ ]]; then
     # Check MCP role and block if operator
   fi
   ```

2. **Separate Project Configs** - Maintain different `.claude/` configurations with different tool allowlists for operator vs developer workflows.

3. **MCP-Only Mode** - A future Claude Code feature could restrict sessions to only use MCP tools, blocking direct filesystem access entirely.

4. **Session Initialization Protocol** - Standardize checking `get_role_info` at conversation start and binding behavior accordingly.

### Intent

The role system is designed to:
- **Operator**: Safe for production use by non-developers. Focus on data ops, ML training, monitoring. Should not modify source code or infrastructure.
- **Developer**: Full access for local development. Can read/write source, access internal docs, run arbitrary commands.

Until enforcement is strengthened, treat operator mode as a **strong hint** rather than a security boundary.
