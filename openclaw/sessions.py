"""Session management for OpenClaw.

File-based JSON storage in ``~/.openclaw/sessions/``, consistent with the
``_projects.json`` pattern used by the LanceDB MCP server.
"""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from config import OpenClawConfig
from errors import SessionError


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Rough estimate: 1 token ≈ 4 characters.
CHARS_PER_TOKEN = 4


@dataclass
class SessionMessage:
    """A single message within a session."""

    role: str  # "user" | "assistant" | "system"
    content: str
    timestamp: str  # ISO 8601 UTC
    token_estimate: int = 0

    def __post_init__(self):
        if self.token_estimate == 0:
            self.token_estimate = max(1, len(self.content) // CHARS_PER_TOKEN)


@dataclass
class Session:
    """A conversation session."""

    session_id: str
    created_at: str  # ISO 8601 UTC
    last_active: str  # ISO 8601 UTC
    channel: str = ""
    status: str = "active"  # "active" | "archived"
    messages: list[SessionMessage] = field(default_factory=list)
    total_tokens: int = 0
    compaction_count: int = 0


# ---------------------------------------------------------------------------
# SessionManager
# ---------------------------------------------------------------------------


class SessionManager:
    """CRUD operations, compaction, and archival for sessions."""

    def __init__(self, config: OpenClawConfig):
        self._config = config
        self._sessions_dir = Path(config.session.sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

    # -- CRUD ----------------------------------------------------------------

    def create_session(self, channel: str = "") -> Session:
        """Create a new session and persist it."""
        now = datetime.now(timezone.utc).isoformat()
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            created_at=now,
            last_active=now,
            channel=channel,
        )
        self._save_session(session)
        return session

    def get_session(self, session_id: str) -> Session:
        """Load a session from disk. Raises ``SessionError`` if not found."""
        path = self._session_path(session_id)
        if not path.exists():
            raise SessionError(
                f"Session '{session_id}' not found",
                context={"session_id": session_id},
            )
        return self._load_session(path)

    def list_sessions(self, status: str | None = None) -> list[Session]:
        """Return all sessions, optionally filtered by status."""
        sessions: list[Session] = []
        for path in sorted(self._sessions_dir.glob("*.json")):
            try:
                s = self._load_session(path)
                if status is None or s.status == status:
                    sessions.append(s)
            except (SessionError, Exception):
                continue
        return sessions

    def add_message(
        self, session_id: str, role: str, content: str
    ) -> SessionMessage:
        """Append a message to a session. Returns the new message."""
        session = self.get_session(session_id)
        now = datetime.now(timezone.utc).isoformat()
        msg = SessionMessage(role=role, content=content, timestamp=now)
        session.messages.append(msg)
        session.total_tokens += msg.token_estimate
        session.last_active = now
        self._save_session(session)
        return msg

    def delete_session(self, session_id: str) -> bool:
        """Delete a session file. Returns ``True`` if removed."""
        path = self._session_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    # -- Compaction ----------------------------------------------------------

    def needs_compaction(self, session_id: str) -> bool:
        """Check if a session exceeds the compaction token threshold."""
        session = self.get_session(session_id)
        return session.total_tokens >= self._config.session.compaction_token_threshold

    def compact_session(self, session_id: str) -> Session:
        """Replace session history with a summary message.

        Uses a simple heuristic: keeps the first and last messages and
        creates a summary marker in between.
        """
        session = self.get_session(session_id)
        if len(session.messages) <= 2:
            return session

        now = datetime.now(timezone.utc).isoformat()
        first = session.messages[0]
        last = session.messages[-1]
        msg_count = len(session.messages)

        summary = SessionMessage(
            role="system",
            content=(
                f"[Session compacted: {msg_count} messages summarized. "
                f"Original span: {first.timestamp} to {last.timestamp}]"
            ),
            timestamp=now,
        )

        session.messages = [first, summary, last]
        session.compaction_count += 1
        session.total_tokens = sum(m.token_estimate for m in session.messages)
        session.last_active = now
        self._save_session(session)
        return session

    # -- Archival ------------------------------------------------------------

    def archive_session(self, session_id: str) -> Session:
        """Mark a session as archived."""
        session = self.get_session(session_id)
        session.status = "archived"
        session.last_active = datetime.now(timezone.utc).isoformat()
        self._save_session(session)
        return session

    def archive_old_sessions(self, older_than_days: int) -> list[str]:
        """Archive sessions older than the given number of days.

        Returns the list of archived session IDs.
        """
        from datetime import timedelta

        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        archived: list[str] = []

        for session in self.list_sessions(status="active"):
            last = datetime.fromisoformat(session.last_active)
            # Ensure timezone-aware comparison.
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if last < cutoff:
                self.archive_session(session.session_id)
                archived.append(session.session_id)

        return archived

    # -- Persistence helpers -------------------------------------------------

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir / f"{session_id}.json"

    def _save_session(self, session: Session) -> None:
        """Persist a session to disk (atomic write)."""
        path = self._session_path(session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = asdict(session)
        data = json.dumps(payload, indent=2) + "\n"

        fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, str(path))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def _load_session(self, path: Path) -> Session:
        """Load a session from a JSON file."""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionError(
                f"Failed to read session at {path}: {exc}",
                context={"path": str(path)},
            ) from exc

        messages = [
            SessionMessage(
                role=m["role"],
                content=m["content"],
                timestamp=m["timestamp"],
                token_estimate=m.get("token_estimate", 0),
            )
            for m in raw.get("messages", [])
        ]

        return Session(
            session_id=raw["session_id"],
            created_at=raw["created_at"],
            last_active=raw["last_active"],
            channel=raw.get("channel", ""),
            status=raw.get("status", "active"),
            messages=messages,
            total_tokens=raw.get("total_tokens", 0),
            compaction_count=raw.get("compaction_count", 0),
        )
