# OpenClaw — Enhances Long Context with LanceDB + Claude AI

A reference architecture and working implementation that gives [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) semantic code search capabilities, overcoming context window limitations on large codebases. The project comprises three components:

1. **LanceDB MCP Server** — Semantic code search over stdio with hybrid vector + full-text search
2. **Agent Team** — 10 specialized Claude agents (via the Claude Agent SDK) for indexing, searching, reviewing, deploying, and more
3. **OpenClaw Gateway** — A self-hosted AI assistant gateway with persistent memory, session management, security, and diagnostics

## Table of Contents

- [The Problem](#the-problem)
- [The Solution](#the-solution)
- [Quick Start](#quick-start)
- [Search Pipeline](#search-pipeline)
  - [Pipeline Details](#pipeline-details)
  - [MCP Tools](#mcp-tools)
  - [Multi-Project Support](#multi-project-support)
  - [Server Lifecycle](#server-lifecycle)
- [Supported Languages](#supported-languages)
- [Configuration](#configuration)
- [Agent Team (Claude Agent SDK)](#agent-team-claude-agent-sdk)
  - [Agent Architecture](#agent-architecture)
  - [Agent Usage](#agent-usage)
- [OpenClaw Gateway](#openclaw-gateway)
  - [Architecture](#architecture)
  - [Deployment](#deployment)
  - [Gateway Configuration](#gateway-configuration)
  - [HTTP API](#http-api)
  - [CLI Usage](#cli-usage)
  - [Security](#security)
  - [Memory System](#memory-system)
- [Key Dependencies](#key-dependencies)
- [Project Structure](#project-structure)
- [Documentation](#documentation)
- [License](#license)

## The Problem

Claude Code's context window is finite. On large repositories, reading every file to find relevant code is slow and expensive. Developers need a way to let Claude search by *meaning* — not just filenames or exact strings — so it can pinpoint the right code without exhausting its context.

## The Solution

This project addresses the problem with three layers that work together: a semantic search server that indexes and searches code by meaning, an agent team that provides specialized capabilities on top of that search, and a gateway that ties everything together with persistent memory and session management.

```
OpenClaw Gateway (HTTP/CLI)
    │
    ▼
Security ─► Sessions ─► Memory (autoRecall/autoCapture)
    │
    ▼
Agent Team (Orchestrator → 9 specialist agents)
    │
    ▼
LanceDB MCP Server (7 tools, multi-project)
    │
    ▼
LanceDB (vector + FTS, per-project tables)
    │
    ▼
Tree-sitter Chunking ◄── File Discovery (.gitignore-aware)
```

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager
- Docker (for MCP server only — no Python/uv required for that component)
- `ANTHROPIC_API_KEY` environment variable (required for agent team and OpenClaw queries)

### Install MCP Server

```bash
cd lancedb-mcp-server && uv sync

# Optional: add Java, C/C++, Ruby, C# grammars
uv sync --extra all-languages
```

### Install Agent Team

```bash
cd agents && uv sync
```

### Install OpenClaw Gateway

```bash
cd openclaw && uv sync

# Interactive setup wizard — configures host, port, auth, directories
uv run openclaw onboard

# Verify installation
uv run openclaw doctor
```

### Configure Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "lancedb-code": {
      "command": "uv",
      "args": ["--directory", "./lancedb-mcp-server", "run", "server.py"]
    }
  }
}
```

### Usage

Once configured, Claude Code automatically has access to the search tools. A typical workflow:

1. **Index your repo**: Claude calls `index_files` to build the search index
2. **Search by meaning**: Claude calls `search_code` with natural language queries
3. **Incremental updates**: After edits, Claude calls `index_files` with changed paths

### Docker

Build and run without installing Python, uv, or any native dependencies on your host.

**Image details:**
- Multi-stage build: builder (Python 3.12 + uv + all deps) → runtime (Python 3.12-slim + git + uv)
- `UV_TORCH_BACKEND=cpu` avoids CUDA libraries, keeping the image at ~2.3 GB
- All tree-sitter grammars included (including optional languages)
- Embedding model pre-downloaded and baked into the image via `scripts/prefetch_model.py`
- LanceDB index persists across container restarts via a named volume

```bash
# Build the image
cd lancedb-mcp-server && docker compose build

# Test that stdio transport responds to MCP initialize
echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"0.1.0"}},"id":1}' \
  | docker run -i --rm \
      -v "$(pwd)/..:/workspace:ro" \
      -v lancedb-data:/data/lancedb \
      lancedb-code-mcp:latest
```

Configure Claude Code to use the Docker image in your project's `.mcp.json`:

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

**Volume mounts:**

| Mount | Container Path | Purpose |
|-------|---------------|---------|
| Source repo | `/workspace` (read-only bind) | Codebase to index |
| LanceDB data | `/data/lancedb` (named volume) | Persistent search index |

> **Note:** Docker on macOS runs in a Linux VM and cannot access Apple Silicon GPUs (MPS/Metal). For the default model (`all-MiniLM-L6-v2`, 22M params), CPU is faster than MPS due to the model's small size, so this is not a limitation in practice.

### Run Tests

```bash
# MCP server integration test (7-step pipeline)
cd lancedb-mcp-server && uv run python test_integration.py

# Agent team validation (imports, config, agent definitions — no API key needed)
cd agents && uv run python test_agents.py

# OpenClaw gateway tests (no API key needed)
cd openclaw && uv run pytest test_openclaw.py -v
```

## Search Pipeline

```
File Discovery → Tree-sitter Parsing → LanceDB Embedding → Hybrid Search
  (respects        (syntax-aware          (auto-embed via      (vector + FTS
   .gitignore)      chunking)              registry)            with RRF)
```

### Pipeline Details

**File Discovery** (`indexer.py`): Walks the repository respecting `.gitignore` rules via `pathspec`. Skips sensitive files (`.env`, keys, certs), large files (>1 MB), and standard build/cache directories (`node_modules`, `__pycache__`, `.venv`, `target`, `dist`, etc.). Supports path-traversal protection for user-provided paths.

**Chunking** (`chunker.py`): For supported languages, Tree-sitter parses source files into an AST and extracts top-level entities (functions, classes, interfaces, structs, enums, traits, impl blocks). Class bodies are decomposed into individual method chunks for granular search. Module-level code not covered by extracted entities is captured separately. Oversized chunks (>2000 chars / ~500 tokens) are split by lines with part numbering. For non-parsed languages, a line-based fallback (50 lines with 5-line overlap) is used.

**Embedding**: LanceDB's embedding registry auto-embeds the `text` field using `sentence-transformers/all-MiniLM-L6-v2`. This model was benchmarked against `all-mpnet-base-v2` (110M) and `BAAI/bge-base-en-v1.5` (109M) and selected for best signal-to-noise discrimination on code search queries and fastest throughput (~2300 sentences/sec on CPU).

**Search** (`server.py`): Supports three modes — `hybrid` (default, vector + FTS with RRF reranking), `vector` (pure semantic), and `fts` (pure keyword). Metadata filters (language, file path prefix, node type) are applied as prefilters. Results include file path, line range, node type, symbol name, relevance score, and a truncated snippet (200 chars).

**Change Detection** (`indexer.py`): SHA-256 content hashing per file. On re-index, unchanged files are skipped. Changed files have their old chunks deleted before new chunks are inserted.

### MCP Tools

The server exposes 7 tools over MCP stdio transport:

| Tool | Purpose |
|------|---------|
| `search_code` | Hybrid vector+FTS search with language, file path, and node_type filters |
| `index_files` | Full or incremental indexing with SHA-256 content-hash change detection |
| `index_status` | Check index health — chunk/file counts, languages, node types, vector/FTS index status |
| `remove_files` | Remove deleted files from the index to keep it consistent |
| `switch_project` | Switch to (or create) a named project context with its own isolated index |
| `list_projects` | List all registered projects with repo roots and table names |
| `remove_project` | Unregister a project and optionally drop its LanceDB table |

All tools accept an optional `project` parameter to target a specific project. When omitted, the active project is used.

### Multi-Project Support

Each project gets its own LanceDB table, isolated from other projects. A `_projects.json` sidecar registry inside the DB directory tracks project metadata.

- **Table naming**: `"default"` → `code_chunks` (legacy compatible), others → `project_{name}`
- **Project names**: letters, digits, underscores, hyphens; 1-63 chars; must start with a letter
- **Legacy fallback**: existing `code_chunks` tables are auto-adopted as the `"default"` project on first startup
- **Registry file**: `{db_path}/_projects.json` (safe — LanceDB only scans for `*.lance/` dirs)

**Multi-project workflow:**

1. `switch_project("backend", repo_root="/path/to/backend")` — create and switch
2. `index_files` — index the backend project
3. `switch_project("frontend", repo_root="/path/to/frontend")` — create another
4. `index_files` — index the frontend project
5. `search_code(query, project="backend")` — search a specific project
6. `list_projects` — see all projects (active marked with `*`)

### Server Lifecycle

On startup, the FastMCP server connects to LanceDB, loads the embedding function from the registry, initializes the multi-project registry (`projects.py`), and opens or creates tables for active projects. On shutdown, tables are compacted via `table.optimize()`. Custom exceptions (`errors.py`) provide structured error reporting for indexing, search, chunking, and project operations. Logging goes to stderr (required for stdio MCP servers).

## Supported Languages

**Tree-sitter syntax-aware chunking** (extracts functions, classes, methods, interfaces, structs, enums, traits, impl blocks):
- Python, JavaScript, TypeScript, TSX, Rust, Go
- Java, C, C++, Ruby, C# *(optional, install with `--extra all-languages`)*

**Line-based fallback chunking** (50 lines, 5-line overlap):
- Markdown, YAML, TOML, JSON, HTML, CSS, SCSS, Shell, SQL, GraphQL, Protobuf, Terraform, Dockerfiles

**Always skipped**:
- Sensitive files: `.env`, `*.pem`, `*.key`, `*.crt`, SSH keys
- Large files: >1 MB
- Build directories: `node_modules`, `__pycache__`, `.venv`, `target`, `build`, `dist`, `vendor`, etc.

## Configuration

All MCP server settings are via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LANCEDB_PATH` | `./.lancedb` | Database directory (relative to repo root if not absolute) |
| `LANCEDB_REPO_ROOT` | `.` (cwd) | Repository root for file scanning |
| `LANCEDB_EMBEDDING_PROVIDER` | `sentence-transformers` | LanceDB embedding registry key |
| `LANCEDB_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Embedding model name (22M params, 384 dims) |
| `LANCEDB_TABLE_NAME` | `code_chunks` | LanceDB table name (default project) |

For OpenClaw gateway configuration, see [Gateway Configuration](#gateway-configuration).

## Agent Team (Claude Agent SDK)

A team of specialized agents built with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) that provides a programmatic interface to the LanceDB code search tools.

### Agent Architecture

```
User Query
    │
    ▼
Orchestrator (sonnet)
    ├── Task → Indexer Agent (haiku)       — index_files, index_status, remove_files
    ├── Task → Searcher Agent (sonnet)     — search_code, Read, Grep, Glob
    ├── Task → Reviewer Agent (opus)       — search_code, Read, Grep, Glob
    ├── Task → Q&A Agent (sonnet)          — search_code, index_status, Read, Grep, Glob
    ├── Task → Deployer Agent (sonnet)     — Bash, Read, Grep, Glob
    ├── Task → Memory Agent (sonnet)       — search_code, index_files, Read, Write
    ├── Task → Security Agent (opus)       — search_code, Read, Grep, Glob, Bash
    ├── Task → DevOps Agent (haiku)        — index_status, list_projects, Bash, Read, Grep
    └── Task → Planner Agent (opus)        — search_code, Read, Grep, Glob, Write, Task
```

The orchestrator routes user queries to the appropriate specialist agent via the `Task` tool. Each agent has focused prompts and access to specific MCP tools from the lancedb-code server. Prompts live in `agents/prompts/*.md` and can be edited without code changes.

| Agent | Role | Model |
|-------|------|-------|
| Orchestrator | Routes queries to specialists | Sonnet |
| Indexer | Full/incremental indexing, status, cleanup | Haiku |
| Searcher | Semantic code search with filters | Sonnet |
| Reviewer | Code quality and security review | Opus |
| Q&A | Codebase explanations and architecture | Sonnet |
| Deployer | Docker builds, config validation | Sonnet |
| Memory | Cross-session episodic memory | Sonnet |
| Security | Vulnerability scanning, secrets detection | Opus |
| DevOps | Health monitoring, diagnostics | Haiku |
| Planner | Implementation plans, task decomposition | Opus |

### Agent Usage

```bash
# Requires ANTHROPIC_API_KEY
cd agents

# Index the repository
uv run python orchestrator.py "Index the repository"

# Search for code
uv run python orchestrator.py "Find all authentication-related code"

# Code review
uv run python orchestrator.py "Review server.py for security issues"

# Codebase Q&A
uv run python orchestrator.py "How does the chunking pipeline work?"

# Docker builds
uv run python orchestrator.py "Build the Docker image and validate"

# Memory
uv run python orchestrator.py "Remember that we chose hybrid search for best recall"

# Security scanning
uv run python orchestrator.py "Scan the codebase for security vulnerabilities"

# DevOps diagnostics
uv run python orchestrator.py "Check system health and index status"

# Planning
uv run python orchestrator.py "Plan how to add WebSocket support"
```

## OpenClaw Gateway

A self-hosted AI assistant gateway that wraps the agent team with persistent memory, session management, security, and diagnostics. All state is file-based — no external database required.

### Architecture

```
CLI / HTTP Client
    │
    ▼
┌─────────────────────────────────────────────┐
│  OpenClaw Gateway (aiohttp)                 │
│                                             │
│  Security ─► Sessions ─► Memory             │
│  (token auth,  (file-based,  (SOUL/MEMORY/  │
│   channel       CRUD,         SESSION-STATE, │
│   pairing,      compaction,   autoRecall,    │
│   blast radius) archival)     autoCapture)   │
│                                             │
│  Diagnostics (health, repair, reports)      │
└─────────────────────────────────────────────┘
    │
    ▼
Agent Team → LanceDB MCP Server
```

### Deployment

After installing dependencies (see [Quick Start](#quick-start)), run the setup wizard and start the gateway:

```bash
cd openclaw

# Interactive setup — configures host, port, auth, directories
uv run openclaw onboard

# Verify everything is wired up correctly
uv run openclaw doctor

# Start the gateway (listens on 127.0.0.1:18789 by default)
uv run openclaw start

# Stop the gateway (sends SIGTERM via PID file)
uv run openclaw stop
```

The `onboard` wizard prompts for gateway host/port, authentication mode, and paths to the `agents/` and `lancedb-mcp-server/` directories. It saves config to `~/.openclaw/openclaw.json` and creates workspace seed files.

#### Production considerations

- **Network**: Default is localhost-only. Set `gateway.host` to `0.0.0.0` for remote access.
- **TLS**: No built-in TLS — use a reverse proxy (nginx, Caddy) for HTTPS in production.
- **Process management**: Use systemd, supervisord, or similar to keep the gateway running. The PID file (`gateway.pid`) supports basic start/stop.
- **Scaling**: File-based sessions scale to 10k+ sessions. Memory entries stored as individual Markdown files. No database backend.
- **Agent invocation**: The gateway invokes agents via subprocess (`uv --directory {agents_dir} run python orchestrator.py`). Requires `ANTHROPIC_API_KEY` in the environment.

#### File layout on disk

```
~/.openclaw/
├── openclaw.json        # Configuration
├── channels.json        # Approved channel pairings
├── gateway.pid          # PID file (while running)
├── gateway.log          # Server logs
└── sessions/
    ├── abc123def456.json  # One file per session
    └── xyz789abc123.json

.memory/                 # In your workspace root
├── SOUL.md              # Workspace identity and purpose
├── MEMORY.md            # Cross-session memory index
├── SESSION-STATE.md     # Current session state
└── 2026-02-24-hybrid-search.md  # Individual memory entries
```

### Gateway Configuration

All settings live in `~/.openclaw/openclaw.json`. The `onboard` wizard generates this file, or you can create it manually:

```json
{
  "gateway": {
    "host": "127.0.0.1",
    "port": 18789,
    "pid_file": "~/.openclaw/gateway.pid",
    "log_file": "~/.openclaw/gateway.log"
  },
  "auth": {
    "token": "",
    "open_access": true
  },
  "memory": {
    "workspace_dir": ".memory",
    "auto_recall": true,
    "auto_capture": true,
    "recall_limit": 5
  },
  "session": {
    "sessions_dir": "~/.openclaw/sessions",
    "compaction_token_threshold": 100000,
    "archive_after_days": 30
  },
  "security": {
    "allowed_workspace_roots": [],
    "channels_file": "~/.openclaw/channels.json",
    "pairing_code_ttl_seconds": 300
  },
  "search": {
    "query_type": "hybrid",
    "limit": 10
  },
  "embedding": {
    "provider": "sentence-transformers",
    "model": "all-MiniLM-L6-v2"
  },
  "agents_dir": "/path/to/agents",
  "mcp_server_dir": "/path/to/lancedb-mcp-server"
}
```

| Section | Key settings |
|---------|-------------|
| `gateway` | `host`, `port` (1-65535), `pid_file`, `log_file` |
| `auth` | `open_access` (true = no auth), `token` (Bearer token) |
| `memory` | `auto_recall` / `auto_capture` toggles, `recall_limit`, `workspace_dir` |
| `session` | `compaction_token_threshold` (min 1000), `archive_after_days` |
| `security` | `allowed_workspace_roots` (blast radius), `pairing_code_ttl_seconds` (min 30) |

### HTTP API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/health` | GET | No | Uptime, active sessions, component status |
| `/api/query` | POST | Yes | Main query (session + autoRecall + agents + autoCapture) |
| `/api/sessions` | GET | Yes | List sessions (`?status=active\|archived`) |
| `/api/sessions/{id}` | GET | Yes | Session detail with messages |
| `/api/pairing` | POST | Yes | Request pairing code for a channel |
| `/api/pairing/approve` | POST | Yes | Approve channel with pairing code |

**Authentication**: When `auth.open_access` is `false`, all endpoints except `/api/health` require an `Authorization: Bearer <token>` header. Tokens are compared using constant-time comparison.

#### Query flow

```bash
curl -X POST http://localhost:18789/api/query \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query": "Find all authentication code", "session_id": "abc123def456"}'
```

The query pipeline:
1. Get or create session (omit `session_id` to start a new one)
2. Record user message
3. **autoRecall** — search memory entries for relevant context, prepend to query
4. **Invoke agent team** — subprocess call to `orchestrator.py` with augmented query
5. Record assistant response
6. **autoCapture** — extract facts from query/response using keyword heuristics, save as memory entries
7. Return `{ "session_id": "...", "response": "..." }`

#### Health check

```bash
curl http://localhost:18789/api/health
```

```json
{
  "status": "ok",
  "uptime_seconds": 3421.7,
  "active_sessions": 3,
  "checks_passed": 7,
  "checks_total": 7
}
```

### CLI Usage

All commands run from the `openclaw/` directory using `uv run openclaw <command>`.

#### Setup and diagnostics

```bash
# Interactive setup wizard
uv run openclaw onboard

# Run diagnostic checks
uv run openclaw doctor

# Auto-repair missing directories and seed files
uv run openclaw doctor --repair
```

`doctor` checks: config file, workspace directory + seed files (SOUL.md, MEMORY.md, SESSION-STATE.md), sessions directory, log directory, MCP server directory, agents directory, and write permissions. Repairable issues are marked `[repairable]` and fixed with `--repair`.

#### Gateway management

```bash
# Start (foreground, logs to file)
uv run openclaw start

# Stop (sends SIGTERM via PID file)
uv run openclaw stop

# View logs (last N lines, optionally follow)
uv run openclaw logs
uv run openclaw logs -n 100
uv run openclaw logs -f
uv run openclaw logs -f -n 50
```

#### Querying

```bash
# New session (session ID printed after response)
uv run openclaw query "Find all authentication code"

# Continue an existing session
uv run openclaw query "Review server.py" --session abc123def456
```

The `query` command runs the full pipeline (autoRecall → agents → autoCapture) and prints the response followed by the session ID.

#### Session management

```bash
# List all sessions (or filter by status)
uv run openclaw sessions list
uv run openclaw sessions list --status=active
uv run openclaw sessions list --status=archived

# Show session detail with messages
uv run openclaw sessions show <session-id>

# Archive sessions inactive for N days
uv run openclaw sessions archive --older-than 7d
```

Sessions are stored as individual JSON files in `~/.openclaw/sessions/`. Each session tracks messages, token estimates, and compaction count. When `total_tokens` exceeds the `compaction_token_threshold` (default 100k), the session is compacted (first + last message kept, summary marker inserted).

#### Channel pairing

```bash
# List approved channels
uv run openclaw pairing list

# Approve a channel with its 6-digit pairing code
uv run openclaw pairing approve discord-bot 123456

# Revoke a channel
uv run openclaw pairing revoke discord-bot
```

Pairing workflow: a client requests a code via `POST /api/pairing`, the admin approves it via CLI, and the channel is saved to `~/.openclaw/channels.json`. Codes expire after `pairing_code_ttl_seconds` (default 300).

### Security

- **Two auth modes**: `open_access: true` (no auth, default) or `open_access: false` (Bearer token required on all endpoints except `/api/health`)
- **Constant-time comparison**: Token and pairing code validation use `secrets.compare_digest()` to prevent timing attacks
- **Channel pairing**: 6-digit codes with configurable TTL, persisted to `channels.json`
- **Blast radius**: `security.allowed_workspace_roots` restricts file operations to specified directory trees. Empty list = no restrictions
- **Tool audit**: Scans MCP tool descriptions for prompt-injection patterns ("ignore previous", "override", "system prompt", etc.)

### Memory System

The memory system provides cross-session persistence via Markdown files in the `.memory/` workspace directory.

**Workspace files**:
- `SOUL.md` — workspace identity and purpose (manually editable)
- `MEMORY.md` — cross-session memory index (manually editable)
- `SESSION-STATE.md` — current session state (auto-updated)

**autoRecall**: Before each agent invocation, queries memory entries using keyword matching (or MCP semantic search if configured). Up to `recall_limit` (default 5) relevant memories are prepended to the query as context.

**autoCapture**: After each agent response, scans query + response for decision/solution/preference/architecture keywords. Matching patterns are saved as dated Markdown files (e.g., `2026-02-24-hybrid-search.md`) with type, tags, summary, and details.

Memory entry types: `decision`, `solution`, `preference`, `context`, `architecture`.

## Key Dependencies

| Package | Component | Purpose |
|---------|-----------|---------|
| `lancedb` >=0.21.2 | MCP Server | Vector database with auto-embedding and hybrid search |
| `mcp[cli]` >=1.6.0 | MCP Server | Model Context Protocol server framework (FastMCP) |
| `sentence-transformers` >=4.0.0 | MCP Server | Embedding model (`all-MiniLM-L6-v2`) |
| `tree-sitter` >=0.23.0 | MCP Server | Syntax-aware code parsing |
| `pathspec` >=0.12.0 | MCP Server | `.gitignore`-compatible file matching |
| `claude-agent-sdk` | Agents | Claude Agent SDK for multi-agent orchestration |
| `aiohttp` >=3.9.0 | OpenClaw | Async HTTP gateway server |
| `click` >=8.1.0 | OpenClaw | CLI framework |

## Project Structure

```
├── lancedb-mcp-server/
│   ├── server.py              # FastMCP entry point, 7 tools, multi-project lifespan
│   ├── projects.py            # Multi-project registry (ProjectState, JSON persistence)
│   ├── chunker.py             # Tree-sitter syntax-aware chunking + line-based fallback
│   ├── indexer.py             # File discovery, content-hash change detection, LanceDB ingestion
│   ├── config.py              # Environment-based configuration and constants
│   ├── errors.py              # Custom exception hierarchy (IndexingError, SearchError, etc.)
│   ├── test_integration.py    # End-to-end integration test (7-step pipeline)
│   ├── test_acceptance.py     # Acceptance tests
│   ├── pyproject.toml         # Dependencies and build config
│   ├── uv.lock                # Locked dependency versions
│   ├── Dockerfile             # Multi-stage Docker build (CPU-only torch, ~2.3 GB image)
│   ├── docker-compose.yml     # Build orchestration + volume definitions
│   ├── .dockerignore          # Excludes .venv, __pycache__, .lancedb from build context
│   └── scripts/
│       └── prefetch_model.py  # Pre-download embedding model at build time
├── agents/
│   ├── orchestrator.py        # Entry point: query routing to specialist agents
│   ├── config.py              # Shared config: models, paths, MCP server, prompt loader
│   ├── test_agents.py         # Validation tests (imports, config, agent definitions)
│   ├── pyproject.toml         # Dependencies (claude-agent-sdk)
│   ├── agents/                # Agent definitions package
│   │   ├── __init__.py        # Exports ALL_AGENTS dict
│   │   ├── indexer.py         # Indexer agent definition
│   │   ├── searcher.py        # Searcher agent definition
│   │   ├── reviewer.py        # Reviewer agent definition
│   │   ├── qa.py              # Q&A agent definition
│   │   ├── deployer.py        # Deployer agent definition
│   │   ├── memory.py          # Memory agent definition
│   │   ├── security.py        # Security agent definition
│   │   ├── devops.py          # DevOps agent definition
│   │   └── planner.py         # Planner agent definition
│   └── prompts/               # System prompts (Markdown, editable without code changes)
│       ├── orchestrator.md
│       ├── indexer.md
│       ├── searcher.md
│       ├── reviewer.md
│       ├── qa.md
│       ├── deployer.md
│       ├── memory.md
│       ├── security.md
│       ├── devops.md
│       └── planner.md
├── openclaw/
│   ├── __init__.py            # Package marker with version
│   ├── errors.py              # Exception hierarchy (OpenClawError and subclasses)
│   ├── config.py              # Dataclass configuration with JSON persistence
│   ├── security.py            # Token auth, channel pairing, blast radius, tool audit
│   ├── sessions.py            # File-based session CRUD, compaction, archival
│   ├── memory.py              # Workspace files (SOUL/MEMORY/SESSION-STATE), autoRecall/Capture
│   ├── diagnostics.py         # Health checks, repair, terminal-friendly reports
│   ├── gateway.py             # Async HTTP gateway (aiohttp, 6 endpoints)
│   ├── cli.py                 # Click-based CLI (onboard, start, stop, doctor, logs, etc.)
│   ├── test_openclaw.py       # Comprehensive test suite (no API keys needed)
│   ├── pyproject.toml         # Package metadata and dependencies
│   └── uv.lock                # Locked dependency versions
├── Docs/
│   ├── Integrating-LanceDB-with-Claude-Code-CLI.md          # Architecture guide (36 citations)
│   └── OpenClaw-LanceDB-Claude-CLI-Integration.md           # System integration guide
├── CLAUDE.md                  # Claude Code project guidance
└── LICENSE                    # Apache 2.0
```

## Documentation

- [`Docs/Integrating-LanceDB-with-Claude-Code-CLI.md`](Docs/Integrating-LanceDB-with-Claude-Code-CLI.md) — Architectural guide covering embedding strategies, chunking approaches, storage backends, and search optimization (36 citations)
- [`Docs/OpenClaw-LanceDB-Claude-CLI-Integration.md`](Docs/OpenClaw-LanceDB-Claude-CLI-Integration.md) — Complete system integration guide for OpenClaw + LanceDB + Claude CLI

## License

Apache 2.0
