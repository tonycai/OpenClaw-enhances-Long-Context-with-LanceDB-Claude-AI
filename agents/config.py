"""Shared configuration for the LanceDB agent team."""

from __future__ import annotations

import logging
import os
import shlex
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

AGENTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENTS_DIR.parent
MCP_SERVER_DIR = Path(
    os.environ.get("LANCEDB_MCP_SERVER_DIR", str(PROJECT_ROOT / "lancedb-mcp-server"))
)
PROMPTS_DIR = AGENTS_DIR / "prompts"

# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------

MODEL_ORCHESTRATOR = "claude-sonnet-4-6"
MODEL_INDEXER = "claude-haiku-4-5"
MODEL_SEARCHER = "claude-sonnet-4-6"
MODEL_REVIEWER = "claude-opus-4-6"
MODEL_QA = "claude-sonnet-4-6"
MODEL_DEPLOYER = "claude-sonnet-4-6"
MODEL_MEMORY = "claude-sonnet-4-6"
MODEL_SECURITY = "claude-opus-4-6"
MODEL_DEVOPS = "claude-haiku-4-5"
MODEL_PLANNER = "claude-opus-4-6"

# ---------------------------------------------------------------------------
# MCP server configuration (stdio transport)
# ---------------------------------------------------------------------------

_mcp_command = os.environ.get("LANCEDB_MCP_COMMAND")

if _mcp_command:
    # Custom command override (e.g., "docker run -i --rm lancedb-code-mcp:latest")
    # Uses shlex.split for safe tokenisation — no shell injection via bash -c.
    _parts = shlex.split(_mcp_command)
    MCP_SERVERS = {
        "lancedb-code": {
            "type": "stdio",
            "command": _parts[0],
            "args": _parts[1:],
        }
    }
else:
    MCP_SERVERS = {
        "lancedb-code": {
            "type": "stdio",
            "command": "uv",
            "args": ["--directory", str(MCP_SERVER_DIR), "run", "server.py"],
        }
    }

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def setup_logging(debug: bool = False) -> None:
    """Configure logging for the agent team CLI."""
    level = logging.DEBUG if debug else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )


def load_prompt(name: str) -> str:
    """Load a prompt file from the prompts/ directory."""
    path = PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt file not found: {path}")
    return path.read_text(encoding="utf-8").strip()
