"""Diagnostics for OpenClaw.

Checks system health, repairs common issues, and produces terminal-friendly
reports.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from config import DEFAULT_CONFIG_DIR, DEFAULT_CONFIG_PATH, OpenClawConfig
from errors import DiagnosticError


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    """Result of a single diagnostic check."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str
    repairable: bool = False


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

STATUS_SYMBOLS = {"ok": "+", "warn": "!", "fail": "x"}


class Diagnostics:
    """Run diagnostic checks and optionally repair issues."""

    def __init__(self, config: OpenClawConfig):
        self._config = config

    def run_all(self) -> list[CheckResult]:
        """Run all diagnostic checks and return results."""
        checks = [
            self._check_config(),
            self._check_workspace(),
            self._check_sessions_dir(),
            self._check_log_dir(),
            self._check_mcp_server(),
            self._check_agents_dir(),
            self._check_permissions(),
        ]
        return checks

    def repair(self, checks: list[CheckResult]) -> list[str]:
        """Attempt to repair issues flagged as repairable.

        Returns a list of actions taken.
        """
        actions: list[str] = []

        for check in checks:
            if check.status == "ok" or not check.repairable:
                continue

            if check.name == "config":
                DEFAULT_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                actions.append(f"Created config directory: {DEFAULT_CONFIG_DIR}")

            elif check.name == "workspace":
                mem_dir = Path(self._config.memory.workspace_dir)
                if not mem_dir.is_absolute():
                    mem_dir = Path.cwd() / mem_dir
                mem_dir.mkdir(parents=True, exist_ok=True)
                actions.append(f"Created workspace directory: {mem_dir}")

            elif check.name == "sessions_dir":
                sessions_dir = Path(self._config.session.sessions_dir)
                sessions_dir.mkdir(parents=True, exist_ok=True)
                actions.append(f"Created sessions directory: {sessions_dir}")

            elif check.name == "log_dir":
                log_dir = Path(self._config.gateway.log_file).parent
                log_dir.mkdir(parents=True, exist_ok=True)
                actions.append(f"Created log directory: {log_dir}")

        return actions

    @staticmethod
    def format_report(checks: list[CheckResult]) -> str:
        """Format check results as a terminal-friendly table."""
        lines = ["OpenClaw Diagnostics", "=" * 40]

        for check in checks:
            symbol = STATUS_SYMBOLS.get(check.status, "?")
            repair_tag = " [repairable]" if check.repairable and check.status != "ok" else ""
            lines.append(f"  [{symbol}] {check.name}: {check.message}{repair_tag}")

        # Summary.
        ok_count = sum(1 for c in checks if c.status == "ok")
        warn_count = sum(1 for c in checks if c.status == "warn")
        fail_count = sum(1 for c in checks if c.status == "fail")
        lines.append("")
        lines.append(f"  {ok_count} ok, {warn_count} warnings, {fail_count} failures")

        return "\n".join(lines)

    # -- Individual checks ---------------------------------------------------

    def _check_config(self) -> CheckResult:
        if DEFAULT_CONFIG_PATH.exists():
            return CheckResult("config", "ok", f"Config found at {DEFAULT_CONFIG_PATH}")
        if DEFAULT_CONFIG_DIR.exists():
            return CheckResult(
                "config", "warn",
                f"Config dir exists but no openclaw.json (using defaults)",
                repairable=False,
            )
        return CheckResult(
            "config", "warn",
            f"Config directory missing: {DEFAULT_CONFIG_DIR}",
            repairable=True,
        )

    def _check_workspace(self) -> CheckResult:
        mem_dir = Path(self._config.memory.workspace_dir)
        if not mem_dir.is_absolute():
            mem_dir = Path.cwd() / mem_dir
        if mem_dir.is_dir():
            seed_files = ["SOUL.md", "MEMORY.md", "SESSION-STATE.md"]
            missing = [f for f in seed_files if not (mem_dir / f).exists()]
            if missing:
                return CheckResult(
                    "workspace", "warn",
                    f"Workspace exists but missing: {', '.join(missing)}",
                    repairable=True,
                )
            return CheckResult("workspace", "ok", f"Workspace at {mem_dir}")
        return CheckResult(
            "workspace", "warn",
            f"Workspace directory missing: {mem_dir}",
            repairable=True,
        )

    def _check_sessions_dir(self) -> CheckResult:
        sessions_dir = Path(self._config.session.sessions_dir)
        if sessions_dir.is_dir():
            count = len(list(sessions_dir.glob("*.json")))
            return CheckResult(
                "sessions_dir", "ok",
                f"Sessions directory at {sessions_dir} ({count} sessions)",
            )
        return CheckResult(
            "sessions_dir", "warn",
            f"Sessions directory missing: {sessions_dir}",
            repairable=True,
        )

    def _check_log_dir(self) -> CheckResult:
        log_dir = Path(self._config.gateway.log_file).parent
        if log_dir.is_dir():
            return CheckResult("log_dir", "ok", f"Log directory at {log_dir}")
        return CheckResult(
            "log_dir", "warn",
            f"Log directory missing: {log_dir}",
            repairable=True,
        )

    def _check_mcp_server(self) -> CheckResult:
        mcp_dir = self._config.mcp_server_dir
        if not mcp_dir:
            return CheckResult(
                "mcp_server", "warn",
                "MCP server directory not configured",
            )
        if Path(mcp_dir).is_dir() and (Path(mcp_dir) / "server.py").exists():
            return CheckResult(
                "mcp_server", "ok",
                f"MCP server found at {mcp_dir}",
            )
        return CheckResult(
            "mcp_server", "fail",
            f"MCP server not found at {mcp_dir}",
        )

    def _check_agents_dir(self) -> CheckResult:
        agents_dir = self._config.agents_dir
        if not agents_dir:
            return CheckResult(
                "agents_dir", "warn",
                "Agents directory not configured",
            )
        if Path(agents_dir).is_dir() and (Path(agents_dir) / "orchestrator.py").exists():
            return CheckResult(
                "agents_dir", "ok",
                f"Agents found at {agents_dir}",
            )
        return CheckResult(
            "agents_dir", "fail",
            f"Agents not found at {agents_dir}",
        )

    def _check_permissions(self) -> CheckResult:
        """Check that key directories are writable."""
        dirs_to_check = [
            ("config", DEFAULT_CONFIG_DIR),
            ("sessions", Path(self._config.session.sessions_dir)),
        ]
        issues: list[str] = []
        for label, d in dirs_to_check:
            if d.exists() and not os.access(str(d), os.W_OK):
                issues.append(f"{label} ({d})")

        if issues:
            return CheckResult(
                "permissions", "fail",
                f"Not writable: {', '.join(issues)}",
            )
        return CheckResult("permissions", "ok", "All directories writable")
