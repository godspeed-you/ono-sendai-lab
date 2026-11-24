"""Transport-neutral backend contract (LAB-020, LAB-021, LAB-083; ADR-0005).

The lifecycle layer owns sandbox identity, generations, leases and fixture
state. A backend owns *where* a generation runs. Everything the lifecycle
stores about a generation is an opaque ``runtime`` mapping plus an endpoint
*context*; nothing outside the backend may interpret host paths, PIDs, unit
names or loopback addresses, because a future remote backend will not have
them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from .errors import LabError


@dataclass
class GenerationSpec:
    sandbox_id: str
    generation_id: str
    number: int
    region: str
    started_at: float
    lease_deadline: float
    max_deadline: float
    idle_seconds: int
    startup_timeout: int
    test_hooks: bool = False


@dataclass
class RuntimeObservation:
    alive: bool
    terminal_state: str | None = None  # FAILED | EXPIRED | STOPPED when not alive
    reason: str = ""
    details: dict = field(default_factory=dict)


class Backend(ABC):
    name: str = "abstract"

    @abstractmethod
    def preflight(self) -> dict:
        """Prove containment and resource controls; raise LabError if unsafe."""

    @abstractmethod
    def start(self, spec: GenerationSpec, logs_dir) -> dict:
        """Start a generation and return its opaque runtime mapping once the
        backend has verified health, signed identity, isolation and limits."""

    @abstractmethod
    def observe(self, runtime: dict) -> RuntimeObservation:
        """Report whether the generation still runs, or how it ended."""

    @abstractmethod
    def stop(self, runtime: dict) -> dict:
        """Stop only the owned generation and verify it is gone."""

    @abstractmethod
    def call(self, runtime: dict, op: str, params: dict | None = None, timeout: float = 90.0) -> dict:
        """Guarded worker operation inside the generation's managed context."""

    @abstractmethod
    def inspect(self, runtime: dict) -> dict:
        """Safe diagnostics (limits, usage); never credentials or host secrets."""

    @abstractmethod
    def cleanup(self, runtime: dict) -> None:
        """Remove backend-private runtime files after verified death."""

    @abstractmethod
    def endpoint_context(self, runtime: dict) -> dict:
        """Endpoint description *in the backend's own context*."""


SUPPORTED_BACKENDS = ("local",)
DOCUMENTED_FUTURE_BACKENDS = ("remote-vm",)


def get_backend(name: str, settings) -> Backend:
    if name == "local":
        from .local_backend import LocalBackend

        return LocalBackend(settings)
    if name in DOCUMENTED_FUTURE_BACKENDS:
        raise LabError(
            "unsupported_backend",
            f"backend {name!r} is a documented future extension and is not implemented in v0.1.0",
            {"supported": list(SUPPORTED_BACKENDS), "design": "docs/adr/0005-backend-boundary-and-future-vm.md"},
        )
    raise LabError("unsupported_backend", f"unknown backend {name!r}", {"supported": list(SUPPORTED_BACKENDS)})
