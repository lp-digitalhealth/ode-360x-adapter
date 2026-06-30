"""Stub 360X receiver — core logic (pure Python, no web framework).

This holds everything the stub does on receipt so it can be unit-tested without
FastAPI/HTTP. `stub_360x_server.py` is a thin FastAPI wrapper over this class.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# 360X transactions the adapter may send outbound, with friendly names.
KNOWN_TRANSACTIONS = {
    "PCC-55": "Referral Request",
    "PCC-56": "Referral Request Status (accept/decline)",
    "PCC-57": "Referral Outcome / Consultation Note",
    "PCC-58": "Referral Cancellation",
    "PCC-59": "Interim Consultation Note",
    "PCC-60": "Appointment Notification",
    "PCC-61": "No-Show Notification",
}


class EnvelopeError(ValueError):
    """Raised when an incoming envelope is malformed or has an unknown transaction.
    The HTTP layer maps this to 422."""


def normalize_envelope(raw: Any) -> dict:
    """Validate and normalize an incoming envelope dict to the adapter's shape.

    Only `transaction` is strictly required; the rest default, matching the
    adapter's OutboundEnvelope.to_dict() output. Raises EnvelopeError on problems.
    """
    if not isinstance(raw, dict):
        raise EnvelopeError("envelope must be a JSON object")

    tx = raw.get("transaction")
    if not tx:
        raise EnvelopeError("envelope missing required field 'transaction'")
    if tx not in KNOWN_TRANSACTIONS:
        raise EnvelopeError(
            f"unknown 360X transaction '{tx}'. Expected one of: "
            f"{', '.join(sorted(KNOWN_TRANSACTIONS))}")

    docs_in = raw.get("documents", []) or []
    if not isinstance(docs_in, list):
        raise EnvelopeError("'documents' must be a list")
    documents = []
    for i, d in enumerate(docs_in):
        if not isinstance(d, dict):
            raise EnvelopeError(f"document[{i}] must be an object")
        documents.append({
            "id": d.get("id", f"doc-{i + 1}"),
            "mime_type": d.get("mime_type", "text/xml"),
            "content": d.get("content", ""),
        })

    return {
        "transaction": tx,
        "sender_direct_address": raw.get("sender_direct_address", ""),
        "recipient_direct_address": raw.get("recipient_direct_address", ""),
        "hl7v2": raw.get("hl7v2", ""),
        "documents": documents,
    }


class Stub360XReceiver:
    """Stateful receiver: stores received messages and returns acknowledgements."""

    def __init__(self):
        self._received: list[dict] = []

    def receive(self, raw: Any) -> dict:
        """Accept one outbound 360X message. Returns an ack dict; raises
        EnvelopeError if the envelope is malformed/unknown."""
        env = normalize_envelope(raw)
        received_at = datetime.now(timezone.utc).isoformat()
        record = {
            "received_at": received_at,
            "transaction": env["transaction"],
            "transaction_name": KNOWN_TRANSACTIONS[env["transaction"]],
            "from": env["sender_direct_address"],
            "to": env["recipient_direct_address"],
            "document_count": len(env["documents"]),
            "envelope": env,
        }
        self._received.append(record)
        return {
            "ack": "received",
            "transaction": env["transaction"],
            "received_at": received_at,
            "message": f"Stub 360X server accepted {env['transaction']}.",
        }

    def received(self, transaction: str | None = None) -> list[dict]:
        if transaction:
            return [r for r in self._received if r["transaction"] == transaction]
        return list(self._received)

    def clear(self) -> int:
        n = len(self._received)
        self._received.clear()
        return n

    def health(self) -> dict:
        return {"status": "ok", "received_count": len(self._received)}
