"""Custom exception hierarchy for OpenClaw."""


class OpenClawError(Exception):
    """Base exception. Carries an optional context dict for structured metadata."""

    def __init__(self, message: str, *, context: dict | None = None):
        self.context = context or {}
        super().__init__(message)

    def __str__(self) -> str:
        base = super().__str__()
        if self.context:
            ctx = ", ".join(f"{k}={v!r}" for k, v in self.context.items())
            return f"{base} [{ctx}]"
        return base


class ConfigError(OpenClawError):
    """Raised when configuration loading or validation fails."""


class GatewayError(OpenClawError):
    """Raised when the HTTP gateway encounters an error."""


class MemoryError_(OpenClawError):
    """Raised when memory operations fail.

    Named ``MemoryError_`` to avoid shadowing the built-in ``MemoryError``.
    """


class SessionError(OpenClawError):
    """Raised when session operations fail."""


class SecurityError_(OpenClawError):
    """Raised when security checks fail.

    Named ``SecurityError_`` to avoid shadowing the built-in.
    """


class DiagnosticError(OpenClawError):
    """Raised when diagnostic checks encounter unrecoverable issues."""
