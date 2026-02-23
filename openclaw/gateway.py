"""Async HTTP gateway for OpenClaw.

Uses aiohttp to serve a lightweight API that routes queries through the
agent team, manages sessions, and handles channel pairing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from config import OpenClawConfig, load_config
from diagnostics import Diagnostics
from errors import GatewayError, SessionError
from memory import MemoryManager
from security import SecurityManager
from sessions import SessionManager

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


class Gateway:
    """OpenClaw HTTP gateway wrapping the agent team."""

    def __init__(self, config: OpenClawConfig | None = None):
        self._config = config or load_config()
        self._security = SecurityManager(self._config)
        self._sessions = SessionManager(self._config)
        self._memory = MemoryManager(self._config)
        self._start_time = time.time()

    def create_app(self) -> web.Application:
        """Build and return the aiohttp Application."""
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_get("/api/health", self._handle_health)
        app.router.add_post("/api/query", self._handle_query)
        app.router.add_get("/api/sessions", self._handle_list_sessions)
        app.router.add_get("/api/sessions/{session_id}", self._handle_get_session)
        app.router.add_post("/api/pairing", self._handle_pairing_request)
        app.router.add_post("/api/pairing/approve", self._handle_pairing_approve)
        return app

    # -- Middleware -----------------------------------------------------------

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler):
        """Enforce token authentication on all routes except /api/health."""
        if request.path == "/api/health":
            return await handler(request)

        headers = dict(request.headers)
        if not self._security.authenticate_request(headers):
            return web.json_response(
                {"error": "Unauthorized"}, status=401
            )
        return await handler(request)

    # -- Handlers ------------------------------------------------------------

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Return gateway health status."""
        uptime = time.time() - self._start_time
        active_sessions = len(self._sessions.list_sessions(status="active"))

        diag = Diagnostics(self._config)
        checks = diag.run_all()
        ok_count = sum(1 for c in checks if c.status == "ok")

        return web.json_response({
            "status": "ok",
            "uptime_seconds": round(uptime, 1),
            "active_sessions": active_sessions,
            "checks_passed": ok_count,
            "checks_total": len(checks),
        })

    async def _handle_query(self, request: web.Request) -> web.Response:
        """Handle a user query.

        Flow: session → autoRecall → agent team → autoCapture → respond.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON body"}, status=400
            )

        query = body.get("query", "").strip()
        if not query:
            return web.json_response(
                {"error": "Missing 'query' field"}, status=400
            )

        session_id = body.get("session_id", "")

        # Get or create session.
        if session_id:
            try:
                session = self._sessions.get_session(session_id)
            except SessionError:
                return web.json_response(
                    {"error": f"Session '{session_id}' not found"}, status=404
                )
        else:
            session = self._sessions.create_session()
            session_id = session.session_id

        # Record user message.
        self._sessions.add_message(session_id, "user", query)

        # autoRecall: prepend relevant memories.
        context = self._memory.auto_recall(query)
        augmented_query = f"{context}\n{query}" if context else query

        # Invoke agent team via subprocess.
        agents_dir = self._config.agents_dir
        if not agents_dir:
            agents_dir = str(
                Path(__file__).resolve().parent.parent / "agents"
            )

        try:
            result = await self._run_agent(agents_dir, augmented_query)
        except Exception as exc:
            logger.error("Agent invocation failed: %s", exc)
            return web.json_response(
                {"error": f"Agent error: {exc}"}, status=502
            )

        # Record assistant response.
        self._sessions.add_message(session_id, "assistant", result)

        # autoCapture: extract and persist facts.
        self._memory.auto_capture(query, result, session_id)

        return web.json_response({
            "session_id": session_id,
            "response": result,
        })

    async def _handle_list_sessions(self, request: web.Request) -> web.Response:
        """List sessions with optional status filter."""
        status = request.query.get("status")
        sessions = self._sessions.list_sessions(status=status)
        return web.json_response({
            "sessions": [
                {
                    "session_id": s.session_id,
                    "created_at": s.created_at,
                    "last_active": s.last_active,
                    "status": s.status,
                    "message_count": len(s.messages),
                    "total_tokens": s.total_tokens,
                }
                for s in sessions
            ]
        })

    async def _handle_get_session(self, request: web.Request) -> web.Response:
        """Get session details."""
        session_id = request.match_info["session_id"]
        try:
            session = self._sessions.get_session(session_id)
        except SessionError:
            return web.json_response(
                {"error": f"Session '{session_id}' not found"}, status=404
            )

        return web.json_response({
            "session_id": session.session_id,
            "created_at": session.created_at,
            "last_active": session.last_active,
            "status": session.status,
            "compaction_count": session.compaction_count,
            "total_tokens": session.total_tokens,
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "timestamp": m.timestamp,
                    "token_estimate": m.token_estimate,
                }
                for m in session.messages
            ],
        })

    async def _handle_pairing_request(self, request: web.Request) -> web.Response:
        """Generate a pairing code for a channel."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON body"}, status=400
            )

        channel = body.get("channel", "").strip()
        if not channel:
            return web.json_response(
                {"error": "Missing 'channel' field"}, status=400
            )

        code = self._security.generate_pairing_code(channel)
        return web.json_response({
            "channel": channel,
            "code": code,
            "ttl_seconds": self._config.security.pairing_code_ttl_seconds,
        })

    async def _handle_pairing_approve(self, request: web.Request) -> web.Response:
        """Approve a channel pairing."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, Exception):
            return web.json_response(
                {"error": "Invalid JSON body"}, status=400
            )

        channel = body.get("channel", "").strip()
        code = body.get("code", "").strip()
        if not channel or not code:
            return web.json_response(
                {"error": "Missing 'channel' and/or 'code'"}, status=400
            )

        if self._security.approve_pairing(channel, code):
            return web.json_response({"status": "approved", "channel": channel})

        return web.json_response(
            {"error": "Invalid or expired pairing code"}, status=403
        )

    # -- Agent subprocess ----------------------------------------------------

    @staticmethod
    async def _run_agent(agents_dir: str, query: str) -> str:
        """Invoke the agent orchestrator via subprocess."""
        proc = await asyncio.create_subprocess_exec(
            "uv", "--directory", agents_dir,
            "run", "python", "orchestrator.py", query,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=120
        )

        if proc.returncode != 0:
            err_msg = stderr.decode().strip() or "Unknown error"
            raise GatewayError(
                f"Agent process exited with code {proc.returncode}: {err_msg}",
                context={"returncode": proc.returncode},
            )

        return stdout.decode().strip()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_gateway(config: OpenClawConfig | None = None) -> None:
    """Start the gateway HTTP server (blocking)."""
    cfg = config or load_config()
    gw = Gateway(cfg)
    app = gw.create_app()
    web.run_app(app, host=cfg.gateway.host, port=cfg.gateway.port)
