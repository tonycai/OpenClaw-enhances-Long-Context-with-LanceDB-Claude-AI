"""Persistent memory management for OpenClaw.

Manages workspace files (SOUL.md, MEMORY.md, SESSION-STATE.md) and
memory entries in the ``.memory/`` directory. Provides autoRecall
(search LanceDB for relevant memories) and autoCapture (extract facts
from conversations via keyword heuristics).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import OpenClawConfig
from errors import MemoryError_


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MemoryEntry:
    """A single memory entry written to ``.memory/``."""

    title: str
    date: str  # YYYY-MM-DD
    type: str  # decision | solution | preference | context | architecture
    tags: list[str] = field(default_factory=list)
    summary: str = ""
    details: str = ""
    related: str = ""

    def to_markdown(self) -> str:
        """Format as the canonical memory entry markdown."""
        lines = [
            f"# Memory: {self.title}",
            f"Date: {self.date}",
            f"Type: {self.type}",
            f"Tags: {', '.join(self.tags)}",
            "",
            "## Summary",
            self.summary,
            "",
            "## Details",
            self.details,
            "",
            "## Related",
            self.related,
        ]
        return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Seed content
# ---------------------------------------------------------------------------

SOUL_SEED = """\
# SOUL.md

This file describes the core identity and purpose of this workspace.

## Purpose
OpenClaw AI assistant workspace — providing persistent memory and
context-aware assistance across sessions.

## Principles
- Accuracy over speed
- Cite sources and memory entries
- Respect project boundaries
"""

MEMORY_SEED = """\
# MEMORY.md

Cross-session memory index. Updated automatically by the memory system.

## Recent Decisions
(none yet)

## Key Context
(none yet)
"""

SESSION_STATE_SEED = """\
# SESSION-STATE.md

Current session state. Updated at the start and end of each session.

## Active Session
(none)

## Last Summary
(none)
"""


# ---------------------------------------------------------------------------
# Keyword heuristics for autoCapture
# ---------------------------------------------------------------------------

CAPTURE_PATTERNS = [
    re.compile(r"\b(decided|chose|picked|selected|opted for)\b", re.IGNORECASE),
    re.compile(r"\b(prefer|preference|we should use)\b", re.IGNORECASE),
    re.compile(r"\b(the fix is|solution is|resolved by|fixed by)\b", re.IGNORECASE),
    re.compile(r"\b(architecture|pattern|approach|strategy)\b", re.IGNORECASE),
    re.compile(r"\b(remember that|note that|important:)\b", re.IGNORECASE),
]


def _extract_type(text: str) -> str:
    """Guess the memory type from text content."""
    lower = text.lower()
    if any(w in lower for w in ("decided", "chose", "picked", "selected", "opted")):
        return "decision"
    if any(w in lower for w in ("fix", "solution", "resolved", "fixed")):
        return "solution"
    if any(w in lower for w in ("prefer", "preference")):
        return "preference"
    if any(w in lower for w in ("architecture", "pattern", "design")):
        return "architecture"
    return "context"


def _slug(title: str) -> str:
    """Generate a filesystem-safe slug from a title."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:60] or "untitled"


# ---------------------------------------------------------------------------
# MemoryManager
# ---------------------------------------------------------------------------


class MemoryManager:
    """Manages workspace files and memory entries."""

    def __init__(self, config: OpenClawConfig, workspace_root: str | Path = "."):
        self._config = config
        self._root = Path(workspace_root).resolve()
        self._memory_dir = self._root / config.memory.workspace_dir

    @property
    def memory_dir(self) -> Path:
        return self._memory_dir

    # -- Workspace setup -----------------------------------------------------

    def ensure_workspace(self) -> list[str]:
        """Create the workspace directory and seed files if absent.

        Returns a list of created file paths.
        """
        created: list[str] = []
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        seeds = {
            "SOUL.md": SOUL_SEED,
            "MEMORY.md": MEMORY_SEED,
            "SESSION-STATE.md": SESSION_STATE_SEED,
        }

        for name, content in seeds.items():
            path = self._memory_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                created.append(str(path))

        return created

    # -- Read workspace files ------------------------------------------------

    def read_soul(self) -> str:
        """Read SOUL.md contents."""
        path = self._memory_dir / "SOUL.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def read_memory(self) -> str:
        """Read MEMORY.md contents."""
        path = self._memory_dir / "MEMORY.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def read_session_state(self) -> str:
        """Read SESSION-STATE.md contents."""
        path = self._memory_dir / "SESSION-STATE.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def update_session_state(self, content: str) -> None:
        """Overwrite SESSION-STATE.md."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        path = self._memory_dir / "SESSION-STATE.md"
        path.write_text(content, encoding="utf-8")

    # -- Memory entries ------------------------------------------------------

    def write_memory_entry(self, entry: MemoryEntry) -> Path:
        """Write a memory entry as a markdown file in ``.memory/``.

        Returns the path to the created file.
        """
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{entry.date}-{_slug(entry.title)}.md"
        path = self._memory_dir / filename
        path.write_text(entry.to_markdown(), encoding="utf-8")
        return path

    def list_entries(self) -> list[Path]:
        """List all memory entry files (excluding seed files)."""
        seeds = {"SOUL.md", "MEMORY.md", "SESSION-STATE.md"}
        return sorted(
            p for p in self._memory_dir.glob("*.md") if p.name not in seeds
        )

    # -- autoRecall ----------------------------------------------------------

    def auto_recall(self, query: str) -> str:
        """Search LanceDB for relevant memories via MCP subprocess.

        Returns a formatted context block string. Falls back to
        local file search if the subprocess fails.
        """
        if not self._config.memory.auto_recall:
            return ""

        # Try local file search as a lightweight recall.
        entries = self.list_entries()
        if not entries:
            return ""

        matches: list[str] = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for entry_path in entries:
            try:
                content = entry_path.read_text(encoding="utf-8")
            except OSError:
                continue
            content_lower = content.lower()
            # Simple relevance: check if any query word appears.
            if any(w in content_lower for w in query_words if len(w) > 2):
                # Extract summary section.
                lines = content.split("\n")
                preview = "\n".join(lines[:10])
                matches.append(f"--- {entry_path.name} ---\n{preview}")

        if not matches:
            return ""

        limited = matches[: self._config.memory.recall_limit]
        header = f"## Recalled Memories ({len(limited)} results)\n\n"
        return header + "\n\n".join(limited) + "\n"

    # -- autoCapture ---------------------------------------------------------

    def auto_capture(
        self, query: str, response: str, session_id: str = ""
    ) -> Path | None:
        """Extract facts from a query/response pair and write a memory entry.

        Uses keyword heuristics to detect capturable content.
        Returns the path to the created entry, or ``None`` if nothing captured.
        """
        if not self._config.memory.auto_capture:
            return None

        combined = f"{query} {response}"
        if not any(p.search(combined) for p in CAPTURE_PATTERNS):
            return None

        # Build the entry.
        mem_type = _extract_type(combined)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Title: first meaningful sentence fragment from the response.
        first_line = response.split("\n")[0].strip()
        title = first_line[:80] if first_line else query[:80]

        # Tags from query words.
        tags = [w for w in query.lower().split() if len(w) > 3][:5]

        entry = MemoryEntry(
            title=title,
            date=today,
            type=mem_type,
            tags=tags,
            summary=first_line or query,
            details=f"Query: {query}\n\nResponse excerpt:\n{response[:500]}",
            related=f"session:{session_id}" if session_id else "",
        )

        return self.write_memory_entry(entry)

    # -- MCP subprocess search -----------------------------------------------

    async def search_memories(
        self,
        query: str,
        limit: int = 5,
        search_type: str = "hybrid",
        mcp_server_dir: str = "",
    ) -> list[dict]:
        """Invoke the LanceDB MCP server via subprocess for semantic search.

        Returns a list of result dicts. Falls back to empty list on failure.
        """
        if not mcp_server_dir:
            mcp_server_dir = self._config.mcp_server_dir
        if not mcp_server_dir or not Path(mcp_server_dir).is_dir():
            return []

        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": "search_code",
                "arguments": {
                    "query": query,
                    "limit": limit,
                    "query_type": search_type,
                },
            },
            "id": 1,
        }

        try:
            proc = await asyncio.create_subprocess_exec(
                "uv", "--directory", mcp_server_dir, "run", "python", "server.py",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(json.dumps(request).encode()),
                timeout=30,
            )
            result = json.loads(stdout.decode())
            return result.get("result", {}).get("content", [])
        except Exception:
            return []
