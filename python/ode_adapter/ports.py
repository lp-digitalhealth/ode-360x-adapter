"""Ports — the abstract interfaces the mapping core depends on.

Ports-and-adapters (hexagonal) design: the engine and the C-CDA<->FHIR mapping
never import a concrete FHIR server or a concrete transport. They depend only on
these interfaces. Concrete implementations live in `ode_adapter/plugins/` and are
selected at runtime by name (see registry.py + config.py). This is what makes the
adapter "plug and play": swap servers or transports without touching the core.

Three ports:
  FhirBackend          — the ODE Native (FHIR R4) side
  IheCodec             — packaging: bytes/dict <-> envelope (XDM, XDR, JSON)
  IheOutboundTransport — sending an outbound 360X message (Direct, HTTP, file)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from .xdm import InboundEnvelope, OutboundEnvelope


class FhirBackend(ABC):
    """Drives an ODE Native FHIR R4 server. One implementation per server flavor."""

    name: str = "fhir-backend"

    @abstractmethod
    def submit_referral_bundle(self, bundle: dict) -> dict:
        """Persist a transaction Bundle; return a transaction-response Bundle."""

    @abstractmethod
    def update_task_status(self, task_id: str, status: str,
                           reason: str | None = None,
                           business_status: str | None = None,
                           outputs: list[dict] | None = None,
                           *, owner: str | dict | None = None,
                           status_reason: dict | None = None,
                           note: str | None = None,
                           period_end: str | None = None) -> dict:
        """Transition a Task (e.g. -> cancelled on inbound PCC-58).

        COW subset: `business_status` sets Task.businessStatus (granular 360X
        progress) and `outputs` becomes Task.output (result/outcome references).

        Reply content (all optional, backward compatible):
          `owner`         -> Task.owner (accepting provider; str ref or Reference)
          `status_reason` -> Task.statusReason (coded CodeableConcept; overrides
                             the free-text `reason`)
          `note`          -> appended to Task.note (acknowledgment / comment)
          `period_end`    -> Task.restriction.period.end (expected timeframe)
        """

    def update_request_status(self, request_id: str, status: str,
                              reason: str | None = None) -> dict:
        """Transition a ServiceRequest (COW authorization lifecycle:
        active -> completed/revoked). Optional; default is a no-op echo."""
        return {"resourceType": "ServiceRequest", "id": request_id, "status": status}

    @abstractmethod
    def get_task(self, task_id: str) -> dict:
        ...

    def fetch_results(self, task_id: str) -> list[dict]:
        """Result resources for a completed/updated Task. Optional."""
        return []

    def find_by_referral(self, referral_id: str) -> list[dict]:
        """Return the resources the bridge wrote for this referral episode, so a
        harness 'inbox' can read the FHIR (not just echo a bundle). Optional;
        default empty (the engine falls back to its per-episode cache)."""
        return []

    def watch_tasks(self, handler: Callable[[dict], None]) -> None:
        """Subscribe to Task changes (FHIR Subscriptions) or poll. Optional.
        Out of scope per the crosswalk; the bridge is message-driven."""
        raise NotImplementedError(f"{self.name} does not implement watch_tasks")


class IheCodec(ABC):
    """Packaging port: turn wire bytes (or a pre-unpacked dict) into an
    InboundEnvelope, and an OutboundEnvelope into wire bytes/dict."""

    name: str = "ihe-codec"

    @abstractmethod
    def unpack(self, raw: bytes | str | dict) -> InboundEnvelope:
        ...

    @abstractmethod
    def pack(self, envelope: OutboundEnvelope) -> Any:
        ...


class IheOutboundTransport(ABC):
    """Sends a packaged outbound 360X message to the medical side."""

    name: str = "ihe-transport"

    @abstractmethod
    def send(self, packaged: Any) -> Any:
        ...
