"""Configuration management for OpenClaw.

Reads from ``~/.openclaw/openclaw.json``, returns sensible defaults when the
file is absent, and provides atomic write for persistence.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from errors import ConfigError

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_DIR = Path.home() / ".openclaw"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "openclaw.json"


# ---------------------------------------------------------------------------
# Configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AuthConfig:
    """Authentication settings."""

    token: str = ""
    open_access: bool = True


@dataclass
class HybridSearchConfig:
    """LanceDB hybrid search tuning."""

    query_type: str = "hybrid"
    limit: int = 10


@dataclass
class EmbeddingConfig:
    """Embedding model settings (passed through to LanceDB MCP server)."""

    provider: str = "sentence-transformers"
    model: str = "all-MiniLM-L6-v2"


@dataclass
class MemoryConfig:
    """Persistent memory settings."""

    workspace_dir: str = ".memory"
    auto_recall: bool = True
    auto_capture: bool = True
    recall_limit: int = 5


@dataclass
class SessionConfig:
    """Session management settings."""

    sessions_dir: str = str(DEFAULT_CONFIG_DIR / "sessions")
    compaction_token_threshold: int = 100_000
    archive_after_days: int = 30


@dataclass
class SecurityConfig:
    """Security settings."""

    allowed_workspace_roots: list[str] = field(default_factory=list)
    channels_file: str = str(DEFAULT_CONFIG_DIR / "channels.json")
    pairing_code_ttl_seconds: int = 300


@dataclass
class GatewayConfig:
    """HTTP gateway settings."""

    host: str = "127.0.0.1"
    port: int = 18789
    pid_file: str = str(DEFAULT_CONFIG_DIR / "gateway.pid")
    log_file: str = str(DEFAULT_CONFIG_DIR / "gateway.log")


@dataclass
class OpenClawConfig:
    """Top-level configuration."""

    gateway: GatewayConfig = field(default_factory=GatewayConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    search: HybridSearchConfig = field(default_factory=HybridSearchConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    security: SecurityConfig = field(default_factory=SecurityConfig)
    agents_dir: str = ""
    mcp_server_dir: str = ""


# ---------------------------------------------------------------------------
# Load / Save / Validate
# ---------------------------------------------------------------------------


def _nested_from_dict(cls, data: dict):
    """Recursively construct a dataclass from a dict, ignoring unknown keys."""
    import dataclasses

    fieldtypes = {f.name: f.type for f in dataclasses.fields(cls)}
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            val = data[f.name]
            # If the field type is another dataclass, recurse
            ftype = eval(fieldtypes[f.name]) if isinstance(fieldtypes[f.name], str) else fieldtypes[f.name]
            if dataclasses.is_dataclass(ftype) and isinstance(val, dict):
                kwargs[f.name] = _nested_from_dict(ftype, val)
            else:
                kwargs[f.name] = val
    return cls(**kwargs)


def load_config(path: str | Path | None = None) -> OpenClawConfig:
    """Load configuration from a JSON file.

    Returns default configuration if the file does not exist.
    Raises ``ConfigError`` if the file exists but is malformed.
    """
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH

    if not config_path.exists():
        return OpenClawConfig()

    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(
            f"Failed to read config at {config_path}: {exc}",
            context={"path": str(config_path)},
        ) from exc

    if not isinstance(raw, dict):
        raise ConfigError(
            f"Malformed config (expected object, got {type(raw).__name__})",
            context={"path": str(config_path)},
        )

    return _nested_from_dict(OpenClawConfig, raw)


def save_config(config: OpenClawConfig, path: str | Path | None = None) -> None:
    """Persist configuration to disk (atomic write)."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    config_path.parent.mkdir(parents=True, exist_ok=True)

    data = json.dumps(asdict(config), indent=2, sort_keys=True) + "\n"

    fd, tmp = tempfile.mkstemp(dir=str(config_path.parent), suffix=".tmp")
    try:
        os.write(fd, data.encode("utf-8"))
        os.close(fd)
        os.replace(tmp, str(config_path))
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def validate_config(config: OpenClawConfig) -> list[str]:
    """Return a list of validation warnings (empty if valid)."""
    warnings: list[str] = []

    if config.gateway.port < 1 or config.gateway.port > 65535:
        warnings.append(f"Invalid port: {config.gateway.port}")

    if config.session.compaction_token_threshold < 1000:
        warnings.append(
            f"Compaction threshold too low: {config.session.compaction_token_threshold}"
        )

    if config.security.pairing_code_ttl_seconds < 30:
        warnings.append(
            f"Pairing code TTL too short: {config.security.pairing_code_ttl_seconds}s"
        )

    if config.memory.recall_limit < 1:
        warnings.append(f"Recall limit must be >= 1, got {config.memory.recall_limit}")

    return warnings
