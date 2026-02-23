"""Click-based CLI for OpenClaw."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import click

from config import OpenClawConfig, load_config, save_config, validate_config
from diagnostics import Diagnostics
from errors import OpenClawError, SessionError
from memory import MemoryManager
from security import SecurityManager
from sessions import SessionManager


@click.group()
@click.version_option(package_name="openclaw")
@click.pass_context
def main(ctx):
    """OpenClaw — self-hosted AI assistant gateway."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config()


# ---------------------------------------------------------------------------
# onboard
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def onboard(ctx):
    """Interactive setup wizard."""
    config: OpenClawConfig = ctx.obj["config"]

    click.echo("OpenClaw Setup Wizard")
    click.echo("=" * 40)

    # Gateway settings.
    host = click.prompt("Gateway host", default=config.gateway.host)
    port = click.prompt("Gateway port", default=config.gateway.port, type=int)
    config.gateway.host = host
    config.gateway.port = port

    # Auth.
    open_access = click.confirm("Enable open access (no token)?", default=True)
    config.auth.open_access = open_access
    if not open_access:
        token = click.prompt("Auth token", hide_input=True)
        config.auth.token = token

    # Paths.
    project_root = Path(__file__).resolve().parent.parent
    default_agents = str(project_root / "agents")
    default_mcp = str(project_root / "lancedb-mcp-server")

    config.agents_dir = click.prompt("Agents directory", default=default_agents)
    config.mcp_server_dir = click.prompt("MCP server directory", default=default_mcp)

    # Validate and save.
    warnings = validate_config(config)
    if warnings:
        click.echo("\nWarnings:")
        for w in warnings:
            click.echo(f"  ! {w}")

    save_config(config)
    click.echo(f"\nConfig saved to {Path.home() / '.openclaw' / 'openclaw.json'}")

    # Ensure workspace.
    mem = MemoryManager(config)
    created = mem.ensure_workspace()
    if created:
        click.echo(f"Created workspace files: {', '.join(Path(p).name for p in created)}")

    click.echo("\nSetup complete. Run 'openclaw doctor' to verify.")


# ---------------------------------------------------------------------------
# start / stop
# ---------------------------------------------------------------------------


@main.command()
@click.pass_context
def start(ctx):
    """Start the gateway server."""
    config: OpenClawConfig = ctx.obj["config"]
    pid_file = Path(config.gateway.pid_file)

    # Check if already running.
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            os.kill(pid, 0)  # Check if process exists.
            click.echo(f"Gateway already running (PID {pid})")
            return
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)

    # Write PID.
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    click.echo(
        f"Starting gateway on {config.gateway.host}:{config.gateway.port}"
    )

    try:
        from gateway import run_gateway
        run_gateway(config)
    except KeyboardInterrupt:
        click.echo("\nShutting down...")
    finally:
        pid_file.unlink(missing_ok=True)


@main.command()
@click.pass_context
def stop(ctx):
    """Stop the gateway server via PID file."""
    config: OpenClawConfig = ctx.obj["config"]
    pid_file = Path(config.gateway.pid_file)

    if not pid_file.exists():
        click.echo("No PID file found — gateway may not be running.")
        return

    try:
        pid = int(pid_file.read_text().strip())
        os.kill(pid, signal.SIGTERM)
        click.echo(f"Sent SIGTERM to PID {pid}")
    except (OSError, ValueError) as exc:
        click.echo(f"Could not stop gateway: {exc}")
    finally:
        pid_file.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@main.command()
@click.option("--repair", is_flag=True, help="Attempt to repair issues")
@click.pass_context
def doctor(ctx, repair):
    """Run diagnostic checks."""
    config: OpenClawConfig = ctx.obj["config"]
    diag = Diagnostics(config)
    checks = diag.run_all()
    click.echo(Diagnostics.format_report(checks))

    if repair:
        actions = diag.repair(checks)
        if actions:
            click.echo("\nRepairs:")
            for a in actions:
                click.echo(f"  -> {a}")
        else:
            click.echo("\nNothing to repair.")


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@main.command()
@click.option("-f", "--follow", is_flag=True, help="Follow log output")
@click.option("-n", "--lines", default=50, help="Number of lines to show")
@click.pass_context
def logs(ctx, follow, lines):
    """View gateway logs."""
    config: OpenClawConfig = ctx.obj["config"]
    log_path = Path(config.gateway.log_file)

    if not log_path.exists():
        click.echo("No log file found.")
        return

    content = log_path.read_text(encoding="utf-8")
    output_lines = content.splitlines()[-lines:]
    for line in output_lines:
        click.echo(line)

    if follow:
        click.echo("--- following (Ctrl+C to stop) ---")
        import time

        last_pos = log_path.stat().st_size
        try:
            while True:
                time.sleep(0.5)
                current_size = log_path.stat().st_size
                if current_size > last_pos:
                    with open(log_path) as f:
                        f.seek(last_pos)
                        new_content = f.read()
                        click.echo(new_content, nl=False)
                    last_pos = current_size
        except KeyboardInterrupt:
            pass


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


@main.group()
def sessions():
    """Session management commands."""
    pass


main.add_command(sessions)


@sessions.command("list")
@click.option("--status", type=click.Choice(["active", "archived"]), default=None)
@click.pass_context
def sessions_list(ctx, status):
    """List sessions."""
    config: OpenClawConfig = ctx.obj["config"]
    mgr = SessionManager(config)
    sess_list = mgr.list_sessions(status=status)

    if not sess_list:
        click.echo("No sessions found.")
        return

    click.echo(f"{'ID':<14} {'Status':<10} {'Messages':<10} {'Last Active'}")
    click.echo("-" * 60)
    for s in sess_list:
        click.echo(
            f"{s.session_id:<14} {s.status:<10} {len(s.messages):<10} {s.last_active}"
        )


@sessions.command("show")
@click.argument("session_id")
@click.pass_context
def sessions_show(ctx, session_id):
    """Show session details."""
    config: OpenClawConfig = ctx.obj["config"]
    mgr = SessionManager(config)

    try:
        session = mgr.get_session(session_id)
    except SessionError as exc:
        click.echo(f"Error: {exc}")
        return

    click.echo(f"Session: {session.session_id}")
    click.echo(f"Status: {session.status}")
    click.echo(f"Created: {session.created_at}")
    click.echo(f"Last Active: {session.last_active}")
    click.echo(f"Tokens: {session.total_tokens}")
    click.echo(f"Compactions: {session.compaction_count}")
    click.echo(f"\nMessages ({len(session.messages)}):")
    for m in session.messages:
        preview = m.content[:100].replace("\n", " ")
        click.echo(f"  [{m.role}] {preview}")


@sessions.command("archive")
@click.option("--older-than", required=True, help="Age threshold (e.g. 7d, 30d)")
@click.pass_context
def sessions_archive(ctx, older_than):
    """Archive old sessions."""
    config: OpenClawConfig = ctx.obj["config"]

    # Parse duration.
    if older_than.endswith("d"):
        try:
            days = int(older_than[:-1])
        except ValueError:
            click.echo(f"Invalid duration: {older_than}")
            return
    else:
        click.echo("Duration must end with 'd' (e.g. 7d)")
        return

    mgr = SessionManager(config)
    archived = mgr.archive_old_sessions(days)
    click.echo(f"Archived {len(archived)} session(s).")


# ---------------------------------------------------------------------------
# pairing
# ---------------------------------------------------------------------------


@main.group()
def pairing():
    """Channel pairing commands."""
    pass


@pairing.command("approve")
@click.argument("channel")
@click.argument("code")
@click.pass_context
def pairing_approve(ctx, channel, code):
    """Approve a channel pairing."""
    config: OpenClawConfig = ctx.obj["config"]
    sec = SecurityManager(config)

    if sec.approve_pairing(channel, code):
        click.echo(f"Channel '{channel}' approved.")
    else:
        click.echo("Invalid or expired pairing code.")


@pairing.command("list")
@click.pass_context
def pairing_list(ctx):
    """List approved channels."""
    config: OpenClawConfig = ctx.obj["config"]
    sec = SecurityManager(config)
    channels = sec.list_channels()

    if not channels:
        click.echo("No approved channels.")
        return

    for ch in channels:
        click.echo(f"  {ch['channel']} (approved at {ch['approved_at']})")


@pairing.command("revoke")
@click.argument("channel")
@click.pass_context
def pairing_revoke(ctx, channel):
    """Revoke an approved channel."""
    config: OpenClawConfig = ctx.obj["config"]
    sec = SecurityManager(config)

    if sec.revoke_channel(channel):
        click.echo(f"Channel '{channel}' revoked.")
    else:
        click.echo(f"Channel '{channel}' not found.")


# ---------------------------------------------------------------------------
# query
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query_text")
@click.option("--session", "session_id", default=None, help="Session ID to continue")
@click.pass_context
def query(ctx, query_text, session_id):
    """Send a query to the agent team."""
    import asyncio

    config: OpenClawConfig = ctx.obj["config"]
    mgr = SessionManager(config)
    mem = MemoryManager(config)

    # Session.
    if session_id:
        try:
            mgr.get_session(session_id)
        except SessionError:
            click.echo(f"Session '{session_id}' not found, creating new.")
            session_id = mgr.create_session().session_id
    else:
        session_id = mgr.create_session().session_id

    mgr.add_message(session_id, "user", query_text)

    # autoRecall.
    context = mem.auto_recall(query_text)
    augmented = f"{context}\n{query_text}" if context else query_text

    # Invoke agent.
    agents_dir = config.agents_dir
    if not agents_dir:
        agents_dir = str(Path(__file__).resolve().parent.parent / "agents")

    async def _run():
        proc = await asyncio.create_subprocess_exec(
            "uv", "--directory", agents_dir,
            "run", "python", "orchestrator.py", augmented,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            return f"Error: {stderr.decode().strip()}"
        return stdout.decode().strip()

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        result = f"Error: {exc}"

    mgr.add_message(session_id, "assistant", result)
    mem.auto_capture(query_text, result, session_id)

    click.echo(result)
    click.echo(f"\n[session: {session_id}]")


if __name__ == "__main__":
    main()
