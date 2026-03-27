# Rename Plan: g2 → Gefion

**Status**: Not started
**Estimated scope**: ~300 files, mostly mechanical find-replace + directory rename
**Risk**: High blast radius — every import, every test, every doc. Must be done in one branch, tested thoroughly before merge.

## Guiding Principles

1. **One atomic branch** — all changes in a single branch, one PR
2. **Automated where possible** — scripted renames, not manual edits
3. **Keep `g2` as an alias** during transition (CLI entry point) so existing scripts don't break
4. **Test at every phase** — run full suite after each major step

## Phase 1: Package & Directory Structure

**Goal**: Rename the Python package from `g2` to `gefion`

| What | From | To |
|------|------|----|
| Package directory | `src/g2/` | `src/gefion/` |
| Package name | `name = "g2"` in `pyproject.toml` | `name = "gefion"` |
| CLI entry point | `g2 = "g2.cli:entrypoint"` | `gefion = "gefion.cli:entrypoint"` |
| CLI alias | — | `gf = "gefion.cli:entrypoint"` (short alias) |
| Egg-info | `src/g2.egg-info/` | Regenerated automatically |

**Steps**:
1. `mv src/g2 src/gefion`
2. Update `pyproject.toml`: package name, entry points, internal deps
3. Global find-replace in `src/gefion/`: `from g2.` → `from gefion.`, `import g2` → `import gefion`
4. Update `__init__.py` if it references `g2`
5. `pip install -e .` to verify package installs
6. Run: `gefion --help` — verify CLI works

**Files**: ~53 Python source files, `pyproject.toml`

## Phase 2: Tests

**Goal**: All test imports and references updated

| What | From | To |
|------|------|----|
| Package imports | `from g2.` / `import g2` | `from gefion.` / `import gefion` |
| CLI invocations | `python -m g2.cli` | `python -m gefion.cli` |
| Test DB name | `g2_test` | `gefion_test` |
| DB credentials in tests | `g2:g2pass` | `gefion:gefionpass` |

**Steps**:
1. Global find-replace in `tests/`: `from g2` → `from gefion`, `g2.cli` → `gefion.cli`
2. Update `tests/conftest.py`: test DB name derivation
3. Update `src/gefion/db/schema.py`: `test_db_url()` logic
4. Update `tests/test_test_db_isolation.py`: explicit `g2_test` assertions
5. Run: `OTEL_ENABLED=false .venv/bin/python -m pytest` — full suite must pass

**Files**: ~156 test files, `conftest.py`, `schema.py`

## Phase 3: Database & Docker

**Goal**: Database names, credentials, and container names updated

| What | From | To |
|------|------|----|
| Database name | `g2` | `gefion` |
| Test database | `g2_test` | `gefion_test` |
| DB user | `g2` | `gefion` |
| DB password | `g2pass` | `gefionpass` |
| Container name | `g2-postgres` | `gefion-postgres` |
| DATABASE_URL | `postgresql://g2:g2pass@localhost:6432/g2` | `postgresql://gefion:gefionpass@localhost:6432/gefion` |

**Steps**:
1. Update `docker-compose.yml`: container name, env vars, health check
2. Update `.env` / `.env.example`: DATABASE_URL
3. Update `mcp-server/docker-compose.yml`
4. Update all hardcoded connection strings in source and config
5. Recreate Docker containers: `docker compose down && docker compose up -d`
6. Create new database: `createdb gefion` or let docker-compose handle it
7. Migrate data from old DB if needed: `pg_dump g2 | psql gefion`
8. Run: `gefion health` — verify connections

**Files**: `docker-compose.yml` (x2), `.env`, `Makefile`, ~30 files with connection strings

**IMPORTANT**: This requires a fresh DB setup or data migration. Document the migration path.

## Phase 4: MCP Server

**Goal**: MCP server renamed, tool prefix updated

| What | From | To |
|------|------|----|
| Package name | `g2-mcp-server` | `gefion-mcp-server` |
| Server name | `Server("g2-mcp-server")` | `Server("gefion-mcp-server")` |
| Claude config key | `"g2": { ... }` | `"gefion": { ... }` |
| Tool filter | `mcp__g2__*` | `mcp__gefion__*` |
| Tool prefix stripping | `mcp__g2__` | `mcp__gefion__` |

**Steps**:
1. Update `mcp-server/pyproject.toml`: package name
2. Update `mcp-server/server.py`: Server name, print statements
3. Update `mcp-server/claude_desktop_config.json`: server key
4. Update `src/gefion/ui/views/assistant.py`: `--allowedTools` filter, prefix stripping
5. Update `mcp-server/README.md`
6. Run: verify MCP server connects and tools are accessible

**Files**: `mcp-server/` (~5 files), `assistant.py`

## Phase 5: Claude Code Integration

**Goal**: Skills, settings, and memory updated

| What | From | To |
|------|------|----|
| Skill files | `.claude/commands/g2.md` | `.claude/commands/gefion.md` |
| Skill files | `.claude/commands/g2-dev.md` | `.claude/commands/gefion-dev.md` |
| Skill files | `.claude/commands/g2-services.md` | `.claude/commands/gefion-services.md` |
| Settings | `.claude/settings.local.json` | Update all g2 references |
| Memory | `.claude/projects/.../memory/` | Update references |
| Skill prefix | `g2-` | `gefion-` (per constitution: skills must be prefixed) |

**Steps**:
1. Rename skill files
2. Update skill content (all `g2` CLI references → `gefion`)
3. Update `.claude/settings.local.json`: command patterns, env vars
4. Update project memory files
5. Update `CLAUDE.md`: all references

**Files**: 3 skill files, `settings.local.json`, `CLAUDE.md`, memory files

## Phase 6: UI

**Goal**: All user-facing text updated

| What | From | To |
|------|------|----|
| Page title | `g2 Trading Analysis` | `Gefion Trading Analysis` |
| Sidebar title | `g2 Trading` | `Gefion` |
| Caption | `g2 Trading Analysis v1.0` | `Gefion v1.0` |
| Operator prompt | `g2 MCP tools` | `Gefion MCP tools` |
| Doc references | `g2 quantitative trading platform` | `Gefion quantitative trading platform` |
| History file | `~/.g2/ai_history.jsonl` | `~/.gefion/ai_history.jsonl` |
| Error file | `~/.g2/ui_errors.jsonl` | `~/.gefion/ui_errors.jsonl` |
| Config dir | `~/.g2/` | `~/.gefion/` |

**Steps**:
1. Update `app.py`: title, sidebar, caption
2. Update all views: documentation text, operator prompt
3. Update `history.py` and `errors.py`: file paths
4. Update `config.py` if it defines `~/.g2/`
5. Run UI, verify all pages render correctly

**Files**: `app.py`, `assistant.py`, `history.py`, `errors.py`, `documentation.py`, `config.py`

## Phase 7: Documentation

**Goal**: Every doc reflects the new name

**Root-level docs**:
- `README.md` — title, installation, all CLI examples
- `CLAUDE.md` — guidelines, test commands, DB URLs
- `AI_INSTRUCTIONS.md` — development rules
- `DEVELOPMENT.md` — development guide
- `CHANGELOG.md` — historical references (leave old entries as-is, add rename entry)

**docs/ folder** (~40 files):
- `USER_GUIDE.md` — all `g2` CLI examples → `gefion`
- `ARCHITECTURE.md` — package names, module paths
- `ML_QUICKSTART.md` — CLI examples
- `MCP_WORKFLOWS.md` — MCP server name, tool examples
- `MCP_PRODUCTION.md` — deployment config, connection strings
- `BACKTESTING.md` — CLI examples
- `STRATEGIES.md` — CLI examples
- `OBSERVABILITY.md` — tracing references
- `TROUBLESHOOTING.md` — diagnostic commands
- `WHITEPAPER_TECHNICAL_ANALYSIS_AND_ML.md` — platform name
- `DATABASE_MIGRATIONS.md` — DB names
- `E2E_TEST_GUIDE.md` — test commands
- `PERFORMANCE*.md` — CLI references
- `TEMPO_QUICKSTART.md` — docker references
- `docs/README.md` — index

**docs/archive/** (~30 files):
- Historical docs — update sparingly (change package name references, leave historical CLI examples)

**Spec-kit** (`.specify/`):
- `constitution.md` — DB references, test commands, skill prefix rule
- `progress.md` — current capabilities
- `backlog.md` — future work items
- `notes.md` — development tips
- `specs/` — existing spec references

**MCP server docs**:
- `mcp-server/README.md` — server name, config examples, tool names

**Steps**:
1. Global find-replace: `g2 ` → `gefion ` (with trailing space to avoid false matches)
2. Targeted replacements: `g2.` → `gefion.`, `g2_test` → `gefion_test`, URLs
3. Manual review of each doc for context-sensitive replacements
4. Update constitution version (minor bump — addition of new name)
5. Add changelog entry documenting the rename

**Files**: ~70 markdown files

## Phase 8: Repository & GitHub

**Goal**: Repo itself renamed

| What | From | To |
|------|------|----|
| Repo directory | `~/src/g2` | `~/src/gefion` |
| GitHub repo | `simonibsen/g2` | `simonibsen/gefion` |
| Git remote | — | Update after GitHub rename |

**Steps**:
1. Rename on GitHub (Settings → Repository name)
2. `mv ~/src/g2 ~/src/gefion`
3. Update git remote: `git remote set-url origin git@github.com:simonibsen/gefion.git`
4. Update Claude Code project memory path
5. GitHub auto-redirects old URLs

**Note**: Do this last, after all code changes are merged.

## Phase 9: Verification

**Full verification checklist**:

- [ ] `pip install -e .` succeeds
- [ ] `gefion --help` shows all commands
- [ ] `gf --help` alias works
- [ ] `gefion init` runs successfully
- [ ] `gefion health` passes all checks
- [ ] Full test suite passes (154+ UI tests, CLI tests, DB tests)
- [ ] MCP server starts and tools are accessible
- [ ] UI launches and all pages render
- [ ] AI Actions: prompt submission, conversation history, work trace all work
- [ ] Docker compose up/down works with new container names
- [ ] `~/.gefion/` directory created for history and errors
- [ ] Claude Code skills (`/gefion`, `/gefion-dev`, `/gefion-services`) work
- [ ] No remaining references to `g2` in source (grep verification)
- [ ] Documentation reads correctly with new name

## Automation Script

Most of this can be automated with a script:

```bash
#!/bin/bash
# rename-g2-to-gefion.sh

OLD="g2"
NEW="gefion"
OLD_PASS="g2pass"
NEW_PASS="gefionpass"

# 1. Rename package directory
mv src/$OLD src/$NEW

# 2. Python imports (all .py files)
find src/ tests/ -name "*.py" -exec sed -i '' "s/from ${OLD}\./from ${NEW}./g" {} +
find src/ tests/ -name "*.py" -exec sed -i '' "s/import ${OLD}/import ${NEW}/g" {} +
find src/ tests/ -name "*.py" -exec sed -i '' "s/python -m ${OLD}/python -m ${NEW}/g" {} +

# 3. Documentation (all .md files)
find . -name "*.md" -not -path "./.git/*" -exec sed -i '' "s/g2 /gefion /g" {} +
# ... (more targeted patterns needed)

# 4. Config files
sed -i '' "s/${OLD}/${NEW}/g" pyproject.toml docker-compose.yml Makefile
sed -i '' "s/${OLD_PASS}/${NEW_PASS}/g" docker-compose.yml

# 5. Claude commands
mv .claude/commands/g2.md .claude/commands/gefion.md
mv .claude/commands/g2-dev.md .claude/commands/gefion-dev.md
mv .claude/commands/g2-services.md .claude/commands/gefion-services.md
```

The script handles ~80% of changes. The remaining 20% needs manual review for:
- Context-sensitive replacements (e.g., "g2" in prose vs code)
- URLs and connection strings
- Historical references in changelog/archive (leave as-is vs update)
- Edge cases like `g2.egg-info` (auto-regenerated)

## Migration Path for Existing Users

1. Back up database: `gefion backup --output ~/.gefion/backups/pre-rename`
2. Pull new code
3. `pip install -e .`
4. Create new database: `docker compose up -d` (creates `gefion` DB)
5. Restore data: `gefion restore --input ~/.gefion/backups/pre-rename`
6. `gefion init` — verify everything works
7. Remove old `~/.g2/` directory when confident
