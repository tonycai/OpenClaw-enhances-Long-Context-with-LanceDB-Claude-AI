# OpenClaw — Enhances Long Context with LanceDB + Claude AI

A reference architecture and working implementation that gives [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) semantic code search capabilities, overcoming context window limitations on large codebases. The project comprises three components:

1. **LanceDB MCP Server** — Semantic code search over stdio with hybrid vector + full-text search
2. **Agent Team** — 10 specialized Claude agents (via the Claude Agent SDK) for indexing, searching, reviewing, deploying, and more
3. **OpenClaw Gateway** — A self-hosted AI assistant gateway with persistent memory, session management, security, and diagnostics

## The Problem

Claude Code's context window is finite. On large repositories, reading every file to find relevant code is slow and expensive. Developers need a way to let Claude search by *meaning* — not just filenames or exact strings — so it can pinpoint the right code without exhausting its context.

## The Solution

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

## Architecture

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
- **Project names**: letters, digits, underscores, hyphens; 1–63 chars; must start with a letter
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

## Quick Start

### Prerequisites

- Python 3.10+
- [uv](https://github.com/astral-sh/uv) package manager
- Or: Docker (no Python/uv required)

### Install MCP Server (Native)

```bash
cd lancedb-mcp-server
uv sync

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

# Interactive setup wizard
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

## Configuration

All MCP server settings are via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `LANCEDB_PATH` | `./.lancedb` | Database directory (relative to repo root if not absolute) |
| `LANCEDB_REPO_ROOT` | `.` (cwd) | Repository root for file scanning |
| `LANCEDB_EMBEDDING_PROVIDER` | `sentence-transformers` | LanceDB embedding registry key |
| `LANCEDB_EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Embedding model name (22M params, 384 dims) |
| `LANCEDB_TABLE_NAME` | `code_chunks` | LanceDB table name (default project) |

## Key Dependencies

| Package | Component | Purpose |
|---------|-----------|---------|
| `lancedb` >=0.21.2 | MCP Server | Vector database with auto-embedding and hybrid search |
| `mcp[cli]` >=1.6.0 | MCP Server | Model Context Protocol server framework (FastMCP) |
| `sentence-transformers` >=4.0.0 | MCP Server | Embedding model (`all-MiniLM-L6-v2`) |
| `tree-sitter` >=0.23.0 | MCP Server | Syntax-aware code parsing |
| `pathspec` >=0.12.0 | MCP Server | `.gitignore`-compatible file matching |
| `claude-agent-sdk` | Agents | Claude Agent SDK for multi-agent orchestration |
| `aiohttp` | OpenClaw | Async HTTP gateway server |
| `click` | OpenClaw | CLI framework |

## Agent Team (Claude Agent SDK)

A team of specialized agents built with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk-python) that provides a programmatic interface to the LanceDB code search tools.

### Architecture

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

A self-hosted AI assistant gateway that wraps the agent team with persistent memory, session management, security, and diagnostics.

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

### HTTP API

| Endpoint | Method | Auth | Description |
|----------|--------|------|-------------|
| `/api/health` | GET | No | Uptime, active sessions, component status |
| `/api/query` | POST | Yes | Main query (session + autoRecall + agents + autoCapture) |
| `/api/sessions` | GET | Yes | List sessions (`?status=active\|archived`) |
| `/api/sessions/{id}` | GET | Yes | Session detail with messages |
| `/api/pairing` | POST | Yes | Request pairing code for a channel |
| `/api/pairing/approve` | POST | Yes | Approve channel with pairing code |

### CLI Commands

```bash
cd openclaw

# Interactive setup
uv run openclaw onboard

# Diagnostics
uv run openclaw doctor
uv run openclaw doctor --repair

# Start/stop gateway
uv run openclaw start
uv run openclaw stop

# View logs
uv run openclaw logs -f -n 100

# Session management
uv run openclaw sessions list
uv run openclaw sessions list --status=active
uv run openclaw sessions show <session-id>
uv run openclaw sessions archive --older-than 7d

# Channel pairing
uv run openclaw pairing list
uv run openclaw pairing approve <channel> <code>
uv run openclaw pairing revoke <channel>

# Direct query (requires ANTHROPIC_API_KEY)
uv run openclaw query "Find all authentication code"
uv run openclaw query "Review server.py" --session <id>
```

### Key Features

- **Token auth + channel pairing**: Bearer-token authentication with channel-based access control and blast-radius limits
- **File-based sessions**: CRUD with compaction and archival, no external database required
- **Persistent memory**: Workspace files (SOUL, MEMORY, SESSION-STATE) with autoRecall and autoCapture
- **Diagnostics**: Health checks, auto-repair, and terminal-friendly reports via `openclaw doctor`

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
│   └── pyproject.toml         # Package metadata and dependencies
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
