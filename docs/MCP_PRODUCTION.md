# MCP Server Production Deployment

Guide for deploying the Gefion MCP server in production.

## Behavioral guidance (always-on)

The MCP server ships tools-first behavioral guidance in its server
instructions, surfaced to every calling agent unconditionally (no role
toggle, no per-session opt-in):

> Prefer the system's tools for every operation — data updates, ML training,
> backtests, queries, and monitoring all have dedicated tools. Do not
> reflexively write or run ad-hoc code to do work a tool already does. SQL via
> `query_database` is read-only (SELECT/WITH only). When you hit a genuine
> limit that no tool covers, say so explicitly and note that it would need
> code — don't paper over the gap.

> **History:** earlier versions gated tools behind a `developer`/`operator`
> role (`GEFION_MCP_ROLE`). That gate was vestigial — it guarded nothing
> dangerous (every destructive tool stayed available to `operator`; the only
> difference was hiding `dev_status`) and gave false assurance. It was removed
> in issue #172. If real production RBAC is ever needed, design it properly
> against the actually-destructive tools and the system's owner-gate model —
> don't resurrect the token version.

## Host capability posture (`system_status`)

The MCP server reads its configuration from `.env` (single source of truth); a
variable already set in the environment — e.g. the Claude Desktop `env` block —
still wins over the file. `system_status` then measures what the host can
afford and returns a `host` block, so an agent works *within* the machine's
affordances (use `--limit`, bound concurrency) rather than reaching past them.
It only advises — it never acts.

| Variable | Default | Meaning |
|---|---|---|
| `GEFION_ENV` | `dev` | Host identity: `dev` \| `production`. Unknown/invalid → `dev` (fail-conservative). A `dev` host bounds data operations regardless of measured headroom. |
| `GEFION_MIN_FREE_DISK_GB` | `20` | Below this free disk, `disk.tight` is set and data ops are bounded. |
| `GEFION_MIN_FREE_MEM_GB` | `2` | Below this available memory, `memory.tight` is set (bound concurrency). |

Capability is **measured** (disk / memory / cpu), not hand-declared; `GEFION_ENV`
only biases policy — so every host is assessed by *which* resource is tight, not
a single blanket "constrained" label. Example `host` block:

```json
"host": {
  "env": "dev",
  "disk":   {"free_gb": 122.6, "tight": false},
  "memory": {"available_gb": 3.1, "tight": true},
  "cpu":    {"count": 8},
  "bounded_data_ops": true,
  "notes": ["Refresh price data with --limit against existing symbols ...",
            "Low available memory (3.1 GB) — bound concurrency ..."]
}
```

## SQL Safety

The `query_database` tool enforces read-only access:

- Only `SELECT` and `WITH` (CTEs) queries allowed
- Blocks: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`, `ALTER`, `TRUNCATE`, `GRANT`, `REVOKE`
- Auto-adds `LIMIT 1000` if not specified

## Deployment Best Practices

### 1. Isolate from Source Code

Run the MCP server in a directory without access to source code:

```bash
# Create isolated deployment directory
mkdir -p /opt/gefion-mcp
cp mcp-server/server.py /opt/gefion-mcp/

# Run from isolated directory
cd /opt/gefion-mcp
python server.py
```

### 2. Configure Claude Desktop

Example `claude_desktop_config.json` for production:

```json
{
  "mcpServers": {
    "gefion-prod": {
      "command": "python",
      "args": ["/opt/gefion-mcp/server.py"],
      "env": {
        "GEFION_ENV": "production",
        "DATABASE_URL": "postgresql://gefion:password@db.example.com:5432/gefion"
      }
    }
  }
}
```

For local development, point at the checkout and a local database:

```json
{
  "mcpServers": {
    "gefion-dev": {
      "command": "python",
      "args": ["/path/to/gefion/mcp-server/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://gefion:gefionpass@localhost:5432/gefion"
      }
    }
  }
}
```

## Enforcing behavior technically

The behavioral guidance above is **advisory** — it steers the LLM, but does not
technically prevent Claude Code's core tools (Read, Edit, Write, Bash) from
touching files. If you need a hard boundary (e.g. an agent operating against a
production host that must not modify source), enforce it outside the MCP server:

1. **Claude Code hooks** — a pre-tool hook can block `Edit`/`Write`/`Bash`.
2. **Separate project configs** — different `.claude/` tool allowlists per
   workflow.
3. **MCP-only sessions** — restrict a session to MCP tools, blocking direct
   filesystem access entirely.

Until such enforcement is in place, treat the guidance as a strong hint, not a
security boundary.
