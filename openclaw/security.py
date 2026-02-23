"""Security manager for OpenClaw.

Handles token authentication, channel pairing, blast-radius restrictions,
and MCP tool-description audit.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import OpenClawConfig
from errors import SecurityError_


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class PairingRequest:
    """A pending channel-pairing request."""

    channel: str
    code: str
    created_at: float  # time.time()
    ttl_seconds: int


@dataclass
class ApprovedChannel:
    """An approved channel."""

    channel: str
    approved_at: float


# ---------------------------------------------------------------------------
# SecurityManager
# ---------------------------------------------------------------------------


class SecurityManager:
    """Handles authentication, channel pairing, and operation restrictions."""

    def __init__(self, config: OpenClawConfig):
        self._config = config
        self._channels_file = Path(config.security.channels_file)
        self._pending: dict[str, PairingRequest] = {}
        self._approved = self._load_channels()

    # -- Token authentication ------------------------------------------------

    def authenticate_request(self, headers: dict[str, str]) -> bool:
        """Return ``True`` if the request passes token authentication.

        If ``open_access`` is enabled in config, always returns ``True``.
        Otherwise checks the ``Authorization: Bearer <token>`` header.
        """
        if self._config.auth.open_access:
            return True

        token = self._config.auth.token
        if not token:
            return False

        auth_header = headers.get("Authorization", headers.get("authorization", ""))
        if not auth_header.startswith("Bearer "):
            return False

        provided = auth_header[7:]
        return secrets.compare_digest(provided, token)

    # -- Channel pairing -----------------------------------------------------

    def generate_pairing_code(self, channel: str) -> str:
        """Generate a 6-digit pairing code for a channel.

        Returns the code. The code is valid for ``pairing_code_ttl_seconds``.
        """
        code = f"{secrets.randbelow(1_000_000):06d}"
        self._pending[channel] = PairingRequest(
            channel=channel,
            code=code,
            created_at=time.time(),
            ttl_seconds=self._config.security.pairing_code_ttl_seconds,
        )
        return code

    def approve_pairing(self, channel: str, code: str) -> bool:
        """Approve a channel pairing if the code is correct and not expired.

        Returns ``True`` on success, ``False`` otherwise.
        """
        req = self._pending.get(channel)
        if req is None:
            return False

        # Check expiry.
        elapsed = time.time() - req.created_at
        if elapsed > req.ttl_seconds:
            del self._pending[channel]
            return False

        if not secrets.compare_digest(code, req.code):
            return False

        # Approve.
        del self._pending[channel]
        self._approved[channel] = ApprovedChannel(
            channel=channel,
            approved_at=time.time(),
        )
        self._save_channels()
        return True

    def is_channel_approved(self, channel: str) -> bool:
        """Return ``True`` if the channel has been paired and approved."""
        return channel in self._approved

    def revoke_channel(self, channel: str) -> bool:
        """Revoke an approved channel. Returns ``True`` if it was removed."""
        if channel in self._approved:
            del self._approved[channel]
            self._save_channels()
            return True
        return False

    def list_channels(self) -> list[dict]:
        """Return a list of approved channel dicts."""
        return [asdict(c) for c in self._approved.values()]

    # -- Blast radius --------------------------------------------------------

    def check_operation_allowed(
        self, operation: str, context: dict
    ) -> bool:
        """Check whether an operation is allowed given the context.

        Currently restricts file operations to configured workspace roots.
        Returns ``True`` if allowed.
        """
        allowed_roots = self._config.security.allowed_workspace_roots
        if not allowed_roots:
            # No restrictions configured — allow all.
            return True

        if operation in ("read_file", "write_file", "delete_file"):
            target = context.get("path", "")
            if not target:
                return False
            target_resolved = str(Path(target).resolve())
            return any(
                target_resolved.startswith(str(Path(root).resolve()))
                for root in allowed_roots
            )

        # Non-file operations are allowed by default.
        return True

    # -- MCP tool audit ------------------------------------------------------

    @staticmethod
    def audit_mcp_tools(descriptions: dict[str, str]) -> list[str]:
        """Detect suspicious patterns in MCP tool descriptions.

        Returns a list of warning strings (empty if clean).
        Checks for common prompt-injection patterns in tool descriptions.
        """
        suspicious_patterns = [
            "ignore previous",
            "disregard",
            "override",
            "system prompt",
            "you are now",
            "forget your instructions",
            "new instructions",
        ]
        warnings: list[str] = []
        for tool_name, desc in descriptions.items():
            lower = desc.lower()
            for pattern in suspicious_patterns:
                if pattern in lower:
                    warnings.append(
                        f"Tool '{tool_name}' description contains suspicious "
                        f"pattern: '{pattern}'"
                    )
        return warnings

    # -- Persistence helpers -------------------------------------------------

    def _load_channels(self) -> dict[str, ApprovedChannel]:
        """Load approved channels from disk."""
        if not self._channels_file.exists():
            return {}

        try:
            raw = json.loads(self._channels_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

        channels: dict[str, ApprovedChannel] = {}
        for name, data in raw.items():
            try:
                channels[name] = ApprovedChannel(
                    channel=data["channel"],
                    approved_at=data["approved_at"],
                )
            except (KeyError, TypeError):
                continue
        return channels

    def _save_channels(self) -> None:
        """Persist approved channels to disk (atomic write)."""
        self._channels_file.parent.mkdir(parents=True, exist_ok=True)

        payload = {name: asdict(ch) for name, ch in self._approved.items()}
        data = json.dumps(payload, indent=2, sort_keys=True) + "\n"

        fd, tmp = tempfile.mkstemp(
            dir=str(self._channels_file.parent), suffix=".tmp"
        )
        try:
            os.write(fd, data.encode("utf-8"))
            os.close(fd)
            os.replace(tmp, str(self._channels_file))
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
