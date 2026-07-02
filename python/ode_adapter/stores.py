"""Correlation store + directory — core stateful services.

These are infrastructure the engine owns (not ports), but both are written behind
simple classes so they can be subclassed for a persistent store or a real provider
directory without touching the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

from .config import settings


@dataclass
class Episode:
    referral_id: str
    direct_message_id: str
    submission_set_id: str
    sender_direct_address: str
    recipient_direct_address: str
    task_id: str | None = None
    service_request_id: str | None = None
    status: str = "requested"
    # COW: granular dental business-status (see config.COW_BUSINESS_STATUS).
    business_status: str | None = None
    # Which side opened this episode in the harness: "dental" or "medical".
    initiated_by: str | None = None
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    history: list[str] = field(default_factory=list)
    # The FHIR the bridge wrote for this episode, so a harness "inbox" can read it
    # (in dry-run there is no live server to query). Newest last.
    inbox: list[dict] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.history.append(f"{datetime.now(timezone.utc).isoformat()} {msg}")

    def add_to_inbox(self, resources: list[dict]) -> None:
        for res in resources or []:
            self.inbox.append({k: v for k, v in res.items()
                               if not k.startswith("_")})


class CorrelationStore:
    """In-memory episode store. Subclass for SQLite/Postgres in production
    (episodes must outlive restarts and tolerate late/out-of-order messages)."""

    def __init__(self):
        self._by_referral: dict[str, Episode] = {}

    def create(self, ep: Episode) -> Episode:
        self._by_referral[ep.referral_id] = ep
        return ep

    def get(self, referral_id: str) -> Episode | None:
        return self._by_referral.get(referral_id)

    def all(self) -> list[dict]:
        return [asdict(e) for e in self._by_referral.values()]


class Directory:
    """Direct address <-> ODE Native endpoint / identity. Seed statically here;
    back with a real provider directory (DNS+LDAP / FHIR Endpoint) in production."""

    def __init__(self, mappings: dict[str, str] | None = None):
        self._direct_to_fhir = mappings or {}

    def fhir_endpoint_for(self, direct_address: str) -> str:
        return self._direct_to_fhir.get(direct_address, settings.ode_native_base_url)
