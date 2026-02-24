# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This project provides both a reference architecture document and a working implementation of a LanceDB MCP server that gives Claude Code CLI semantic code search capabilities, overcoming context window limitations on large codebases. It also includes the OpenClaw integration layer — a self-hosted AI assistant gateway with persistent memory, session management, security, and diagnostics.

## Repository Structure

- `lancedb-mcp-server/` — The MCP server implementation (Python)
  - `server.py` — FastMCP entry point, 7 tools, multi-project lifespan management
  - `projects.py` — Multi-project registry (ProjectState, JSON persistence, name validation)
  - `chunker.py` — Tree-sitter syntax-aware chunking + line-based fallback
  - `indexer.py` — File discovery, content-hash change detection, LanceDB ingestion
  - `config.py` — Environment-based configuration
  - `errors.py` — Custom exception hierarchy (IndexingError, SearchError, ChunkingError, ProjectError)
  - `test_integration.py` — End-to-end integration test (discovery, chunking, indexing, search, multi-project)
  - `test_acceptance.py` — Acceptance tests
  - `pyproject.toml` — Dependencies and build config
  - `uv.lock` — Locked dependency versions
  - `Dockerfile` — Multi-stage build: builder (uv + deps + model prefetch) → runtime (slim + git)
  - `docker-compose.yml` — Build orchestration and volume definitions
  - `.dockerignore` — Excludes .venv, __pycache__, .lancedb from build context
  - `scripts/prefetch_model.py` — Pre-downloads embedding model during image build
- `agents/` — Claude Agent SDK agent team (Python)
  - `orchestrator.py` — Entry point: query routing to specialist agents
  - `config.py` — Shared config: models, paths, MCP server, prompt loader
  - `test_agents.py` — Validation tests (imports, config, agent definitions)
  - `pyproject.toml` — Dependencies (claude-agent-sdk)
  - `agents/` — Agent definitions package (indexer, searcher, reviewer, qa, deployer, memory, security, devops, planner)
  - `prompts/` — System prompts (Markdown, editable without code changes)
- `openclaw/` — OpenClaw integration layer (Python)
  - `__init__.py` — Package marker with version
  - `errors.py` — Exception hierarchy (OpenClawError and subclasses)
  - `config.py` — Dataclass configuration with JSON persistence
  - `security.py` — Token auth, channel pairing, blast radius, tool audit
  - `sessions.py` — File-based session CRUD, compaction, archival
  - `memory.py` — Workspace files (SOUL/MEMORY/SESSION-STATE), autoRecall, autoCapture
  - `diagnostics.py` — Health checks, repair, terminal-friendly reports
  - `gateway.py` — Async HTTP gateway (aiohttp, 6 endpoints)
  - `cli.py` — Click-based CLI (onboard, start, stop, doctor, logs, sessions, pairing, query)
  - `pyproject.toml` — Package metadata and dependencies
  - `test_openclaw.py` — Comprehensive test suite (no API keys needed)
  - `uv.lock` — Locked dependency versions
- `Docs/` — Architecture guides
  - `Integrating-LanceDB-with-Claude-Code-CLI.md` — Architecture guide (36 citations)
  - `OpenClaw-LanceDB-Claude-CLI-Integration.md` — System integration guide
- `.mcp.json` — MCP server definitions (gitignored)
- `.claude/settings.local.json` — Claude Code local settings
- `CLAUDE.md` — This file (project guidance for Claude Code)

## Architecture

The server follows a pipeline: **file discovery** (respecting .gitignore) → **Tree-sitter parsing** (syntax-aware chunks for Python, JS, TS, Rust, Go) → **auto-embedding** via LanceDB registry → **hybrid search** with Reciprocal Rank Fusion.

Key design decisions:
- Table-per-project isolation with a `_projects.json` sidecar registry
- Content-hash (SHA-256) change detection skips unchanged files on re-index
- Oversized chunks (>2000 chars) are split by lines with part numbering
- Class bodies are decomposed into individual method chunks for granular search
- Module-level code not covered by extracted entities is captured separately
- Sensitive files (.env, keys, certs) and large files (>1 MB) are always skipped
- Embedding model `all-MiniLM-L6-v2` (22M params, 384 dims) chosen for best signal-to-noise discrimination on code search and fastest throughput — benchmarked against `all-mpnet-base-v2` and `BAAI/bge-base-en-v1.5`

Search results are compact (file path, line range, node type, symbol name, truncated snippet) so Claude can decide what to `Read` in full without wasting context tokens.

### Deployment options

| Method | Command | GPU | Use case |
|--------|---------|-----|----------|
| Native | `uv run server.py` | MPS (Apple Silicon) | Local development |
| Docker | `docker run -i --rm ...` | CPU only | Portability, CI, no local deps |

## MCP Server Commands

```bash
# Install dependencies
cd lancedb-mcp-server && uv sync

# Run the MCP server directly (stdio transport)
cd lancedb-mcp-server && uv run server.py

# Run the integration test
cd lancedb-mcp-server && uv run python test_integration.py

# Install with optional language grammars (Java, C/C++, Ruby, C#)
cd lancedb-mcp-server && uv sync --extra all-languages
```

## Agent Team Commands

```bash
# Install agent dependencies
cd agents && uv sync

# Run validation tests (imports, config, agent definitions — no API key needed)
cd agents && uv run python test_agents.py

# Run the orchestrator with a query (requires ANTHROPIC_API_KEY)
cd agents && uv run python orchestrator.py "Index the repository"
cd agents && uv run python orchestrator.py "Find all authentication-related code"
cd agents && uv run python orchestrator.py "Review server.py for issues"
cd agents && uv run python orchestrator.py "How does the chunking pipeline work?"
cd agents && uv run python orchestrator.py "Build the Docker image and validate"
cd agents && uv run python orchestrator.py "Remember that we chose hybrid search for best recall"
cd agents && uv run python orchestrator.py "Scan the codebase for security vulnerabilities"
cd agents && uv run python orchestrator.py "Check system health and index status"
cd agents && uv run python orchestrator.py "Plan how to add WebSocket support"
```

### Agent Team Architecture

The agent team uses the Claude Agent SDK with 9 specialist subagents:

| Agent | Role | Model | Tools |
|-------|------|-------|-------|
| Orchestrator | Routes queries to specialists | Sonnet | Task |
| Indexer | Full/incremental indexing, status | Haiku | index_files, index_status, remove_files |
| Searcher | Semantic code search | Sonnet | search_code, index_status, Read, Grep, Glob |
| Reviewer | Code quality/security review | Opus | search_code, Read, Grep, Glob |
| Q&A | Codebase explanations | Sonnet | search_code, index_status, Read, Grep, Glob |
| Deployer | Docker builds, config validation | Sonnet | Bash, Read, Grep, Glob |
| Memory | Cross-session episodic memory | Sonnet | search_code, index_files, index_status, switch_project, list_projects, Read, Write |
| Security | Vulnerability scanning, secrets detection | Opus | search_code, Read, Grep, Glob, Bash |
| DevOps | Health monitoring, diagnostics | Haiku | index_status, list_projects, Bash, Read, Grep |
| Planner | Implementation plans, task decomposition | Opus | search_code, Read, Grep, Glob, Write, Task |

All agents connect to the lancedb-code MCP server via stdio transport. Prompts are in `agents/prompts/*.md` (editable without code changes).

## OpenClaw Gateway Commands

```bash
# Install dependencies
cd openclaw && uv sync

# Run all validation tests (no API key needed)
cd openclaw && uv run pytest test_openclaw.py -v

# Interactive setup wizard
cd openclaw && uv run openclaw onboard

# Run diagnostics
cd openclaw && uv run openclaw doctor
cd openclaw && uv run openclaw doctor --repair

# Start/stop gateway
cd openclaw && uv run openclaw start
cd openclaw && uv run openclaw stop

# View logs
cd openclaw && uv run openclaw logs -f -n 100

# Session management
cd openclaw && uv run openclaw sessions list
cd openclaw && uv run openclaw sessions list --status=active
cd openclaw && uv run openclaw sessions show <session-id>
cd openclaw && uv run openclaw sessions archive --older-than 7d

# Channel pairing
cd openclaw && uv run openclaw pairing list
cd openclaw && uv run openclaw pairing approve <channel> <code>
cd openclaw && uv run openclaw pairing revoke <channel>

# Direct query (requires ANTHROPIC_API_KEY for agent invocation)
cd openclaw && uv run openclaw query "Find all authentication code"
cd openclaw && uv run openclaw query "Review server.py" --session <id>
```

### OpenClaw Configuration

Config file: `~/.openclaw/openclaw.json` (generated by `openclaw onboard`, or create manually).

Key settings:
- `gateway.host` / `gateway.port` — Server bind address (default `127.0.0.1:18789`)
- `auth.open_access` — `true` disables auth (default), `false` requires Bearer token
- `auth.token` — Bearer token for API authentication
- `memory.auto_recall` / `memory.auto_capture` — Toggle memory features (default `true`)
- `memory.workspace_dir` — Memory workspace path (default `.memory`)
- `session.sessions_dir` — Session storage path (default `~/.openclaw/sessions`)
- `session.compaction_token_threshold` — Token count before session compaction (default 100000)
- `security.allowed_workspace_roots` — Blast radius for file operations (default `[]` = unrestricted)
- `agents_dir` / `mcp_server_dir` — Paths to agent team and MCP server directories

### Gateway HTTP API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/health` | GET | No | Uptime, active sessions, component status |
| `/api/query` | POST | Yes | Main query (session + autoRecall + agents + autoCapture) |
| `/api/sessions` | GET | Yes | List sessions (`?status=active\|archived`) |
| `/api/sessions/{id}` | GET | Yes | Session detail with messages |
| `/api/pairing` | POST | Yes | Request pairing code for a channel |
| `/api/pairing/approve` | POST | Yes | Approve channel with pairing code |

## Docker Commands

```bash
# Build the Docker image (includes all grammars + pre-downloaded model)
cd lancedb-mcp-server && docker compose build

# Test stdio transport responds to MCP initialize
echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}},"id":1}' \
  | docker run -i --rm \
      -v "$(pwd)/..:/workspace:ro" \
      -v lancedb-data:/data/lancedb \
      lancedb-code-mcp:latest

# One-shot run via docker compose
cd lancedb-mcp-server && docker compose run --rm mcp
```

### Docker `.mcp.json` example

To use the Docker image with Claude Code, add this to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "lancedb-code": {
      "command": "bash",
      "args": [
        "-c",
        "docker run -i --rm -v \"$(pwd):/workspace:ro\" -v lancedb-data:/data/lancedb lancedb-code-mcp:latest"
      ]
    }
  }
}
```

**Key Docker notes:**
- Use `docker run -i` (interactive stdin, no TTY) — TTY corrupts the JSON-RPC binary stream
- The source repo is bind-mounted read-only at `/workspace`
- The LanceDB index persists in the `lancedb-data` named volume at `/data/lancedb`
- The embedding model is baked into the image (no download on first run)
- `UV_TORCH_BACKEND=cpu` keeps the image at ~2.3 GB (vs ~3+ GB with CUDA)
- Docker on macOS cannot access Apple Silicon GPUs — use native `uv run server.py` for MPS

## MCP Server: lancedb-code

The `lancedb-code` server exposes 7 tools over stdio:

| Tool | Purpose |
|------|---------|
| `search_code` | Hybrid vector+FTS search with language/path/node_type filters |
| `index_files` | Index repo (full or specific paths), with content-hash change detection |
| `index_status` | Check chunk count, file count, languages, index health |
| `remove_files` | Remove deleted files from the index |
| `switch_project` | Switch to (or create) a named project context |
| `list_projects` | List all registered projects with repo roots and table names |
| `remove_project` | Unregister a project (optionally drop its LanceDB table) |

All tools accept an optional `project` parameter to target a specific project. When omitted, the active project is used.

### Multi-project support

Each project gets its own LanceDB table, isolated from other projects. A `_projects.json` sidecar registry inside the DB directory tracks project metadata.

- **Table naming**: `"default"` → `code_chunks` (legacy compatible), others → `project_{name}`
- **Project names**: letters, digits, underscores, hyphens; 1-63 chars; must start with a letter
- **Legacy fallback**: existing `code_chunks` tables are auto-adopted as the `"default"` project on first startup
- **Registry file**: `{db_path}/_projects.json` (safe — LanceDB only scans for `*.lance/` dirs)

### Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `LANCEDB_PATH` | `./.lancedb` | Database directory |
| `LANCEDB_REPO_ROOT` | `.` (cwd) | Repository root for the default project |
| `LANCEDB_EMBEDDING_PROVIDER` | `sentence-transformers` | LanceDB registry key |
| `LANCEDB_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Embedding model name |
| `LANCEDB_TABLE_NAME` | `code_chunks` | Default LanceDB table name |

### Supported Languages

**Tree-sitter syntax-aware chunking** (extracts functions, classes, methods, etc.):
Python, JavaScript, TypeScript, TSX, Rust, Go, Java*, C*, C++*, Ruby*, C#*

**Line-based fallback chunking**:
Markdown, YAML, TOML, JSON, HTML, CSS, SCSS, Shell scripts, SQL, GraphQL, Protobuf, Terraform, Dockerfiles

*\* Requires `uv sync --extra all-languages`*

### Typical workflow (single project)

1. Call `index_files` (no args) to index the full repository
2. Call `search_code` with a semantic query to find relevant code
3. After editing files, call `index_files` with the changed paths for incremental update

### Multi-project workflow

1. Call `switch_project("backend", repo_root="/path/to/backend")` to create and switch to a project
2. Call `index_files` to index the backend project
3. Call `switch_project("frontend", repo_root="/path/to/frontend")` to create another project
4. Call `index_files` to index the frontend project
5. Call `search_code(query, project="backend")` to search a specific project
6. Call `list_projects` to see all registered projects (active marked with `*`)
7. Call `switch_project("backend")` to switch back without re-creating

## MCP Servers

Three MCP servers are configured:

- **context7** — Up-to-date library documentation retrieval
- **sequential-thinking** — Structured multi-step reasoning
- **lancedb-code** — Semantic code search (this project)

## License

Apache 2.0
