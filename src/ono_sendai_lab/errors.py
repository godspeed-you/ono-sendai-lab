"""Stable lab error codes and their exit-status classes (LAB-052, ADR-0007)."""

from __future__ import annotations

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_CONFLICT = 3
EXIT_REFUSED = 4
EXIT_UPSTREAM = 5
EXIT_INTERNAL = 6

# code -> (exit status, retryable)
ERROR_CODES: dict[str, tuple[int, bool]] = {
    "usage_error": (EXIT_USAGE, False),
    "invalid_name": (EXIT_USAGE, False),
    "invalid_argument": (EXIT_USAGE, False),
    "operation_not_allowed": (EXIT_USAGE, False),
    "unknown_scenario": (EXIT_USAGE, False),
    "not_found": (EXIT_CONFLICT, False),
    "name_conflict": (EXIT_CONFLICT, False),
    "stale_generation": (EXIT_CONFLICT, False),
    "not_ready": (EXIT_CONFLICT, False),
    "lease_expired": (EXIT_CONFLICT, False),
    "fixture_state_conflict": (EXIT_CONFLICT, False),
    "lock_timeout": (EXIT_CONFLICT, True),
    "preflight_failed": (EXIT_REFUSED, False),
    "isolation_unavailable": (EXIT_REFUSED, False),
    "resource_control_unavailable": (EXIT_REFUSED, False),
    "unsupported_backend": (EXIT_REFUSED, False),
    "slot_busy": (EXIT_REFUSED, True),
    "disk_budget_exceeded": (EXIT_REFUSED, False),
    "lease_cap_exceeded": (EXIT_REFUSED, False),
    "install_invalid": (EXIT_REFUSED, False),
    "unsafe_configuration": (EXIT_REFUSED, False),
    "upstream_error": (EXIT_UPSTREAM, False),
    "aws_error": (EXIT_UPSTREAM, False),
    "transport_error": (EXIT_UPSTREAM, True),
    "timeout": (EXIT_UPSTREAM, True),
    "startup_failed": (EXIT_UPSTREAM, False),
    "seed_failed": (EXIT_UPSTREAM, False),
    "verification_failed": (EXIT_UPSTREAM, False),
    "lifecycle_failure": (EXIT_INTERNAL, False),
    "cleanup_failed": (EXIT_INTERNAL, False),
    "internal_error": (EXIT_INTERNAL, False),
}


class LabError(Exception):
    """An error with a stable code, a safe message and optional safe details."""

    def __init__(self, code: str, message: str, details: dict | None = None):
        if code not in ERROR_CODES:
            raise ValueError(f"unregistered error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    @property
    def exit_code(self) -> int:
        return ERROR_CODES[self.code][0]

    @property
    def retryable(self) -> bool:
        return ERROR_CODES[self.code][1]

    def to_dict(self) -> dict:
        from .redact import redact_obj

        return {
            "code": self.code,
            "message": redact_obj(self.message),
            "retryable": self.retryable,
            "exit_code": self.exit_code,
            "details": redact_obj(self.details),
        }
