"""Stub 360X sender — the stub acting as the medical-side Referral Initiator.

Builds the inbound 360X messages the adapter accepts and POSTs them to the adapter's
inbound endpoint, so a referral can be driven *from the medical side*:

    stub_360x_sender  --(PCC-55 / PCC-58)-->  POST /360x/inbound  -->  adapter

The envelope-building functions are pure (no HTTP) and use the shared `hl7v2` builder
so the adapter parses the referral id correctly. `send_envelope` is the thin HTTP layer.

CLI:
    python -m tools.stub_360x_sender --referral REF-1001 \
        --url http://localhost:8000/360x/inbound
    python -m tools.stub_360x_sender --referral REF-1001 --cancel \
        --url http://localhost:8000/360x/inbound
"""
from __future__ import annotations

import os
import uuid
from typing import Any

from ode_adapter import hl7v2

DEFAULT_SENDER = "referrals@oncology.direct.example.org"
DEFAULT_RECIPIENT = "intake@dentalgroup.direct.example.org"
DEFAULT_ADAPTER_INBOUND_URL = os.getenv(
    "STUB_ADAPTER_INBOUND_URL", "http://localhost:8000/360x/inbound")

_SAMPLE_CDA = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "samples", "referral_request.xml")


def load_sample_cda() -> str:
    """The canonical sample C-CDA Referral Note (head & neck dental clearance)."""
    with open(_SAMPLE_CDA, encoding="utf-8") as fh:
        return fh.read()


def _msg_id() -> str:
    return f"<{uuid.uuid4()}@stub-360x>"


def build_referral_request(referral_id: str = "REF-1001", *,
                           sender_direct_address: str = DEFAULT_SENDER,
                           recipient_direct_address: str = DEFAULT_RECIPIENT,
                           cda_content: str | None = None,
                           direct_message_id: str | None = None,
                           submission_set_id: str | None = None) -> dict:
    """Build a PCC-55 Referral Request envelope (the initiating referral)."""
    cda = cda_content if cda_content is not None else load_sample_cda()
    return {
        "direct_message_id": direct_message_id or _msg_id(),
        "submission_set_id": submission_set_id or f"urn:ohia:submissionset:{referral_id}",
        "sender_direct_address": sender_direct_address,
        "recipient_direct_address": recipient_direct_address,
        "transaction": "PCC-55",
        "hl7v2": hl7v2.build("OMG^O19", referral_id=referral_id),
        "documents": [{"id": "doc-1", "mime_type": "text/xml", "content": cda}],
    }


def build_cancellation(referral_id: str, *,
                       sender_direct_address: str = DEFAULT_SENDER,
                       recipient_direct_address: str = DEFAULT_RECIPIENT,
                       direct_message_id: str | None = None,
                       submission_set_id: str | None = None) -> dict:
    """Build a PCC-58 Referral Cancellation envelope for an existing referral."""
    return {
        "direct_message_id": direct_message_id or _msg_id(),
        "submission_set_id": submission_set_id or f"urn:ohia:submissionset:{referral_id}",
        "sender_direct_address": sender_direct_address,
        "recipient_direct_address": recipient_direct_address,
        "transaction": "PCC-58",
        "hl7v2": hl7v2.build("OSU^O51", referral_id=referral_id, order_status="CA"),
        "documents": [],
    }


def send_envelope(envelope: dict, url: str = DEFAULT_ADAPTER_INBOUND_URL) -> Any:
    """POST an envelope to the adapter's inbound endpoint (thin HTTP layer)."""
    import httpx
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, json=envelope,
                           headers={"Content-Type": "application/json"})
        resp.raise_for_status()
        return resp.json() if resp.content else {"status": resp.status_code}


def send_referral_request(referral_id: str = "REF-1001",
                          url: str = DEFAULT_ADAPTER_INBOUND_URL, **kw) -> Any:
    return send_envelope(build_referral_request(referral_id, **kw), url)


def send_cancellation(referral_id: str,
                      url: str = DEFAULT_ADAPTER_INBOUND_URL, **kw) -> Any:
    return send_envelope(build_cancellation(referral_id, **kw), url)


def _main() -> None:
    import argparse
    import json
    p = argparse.ArgumentParser(description="Send a 360X message to the adapter.")
    p.add_argument("--referral", default="REF-1001", help="referral id")
    p.add_argument("--url", default=DEFAULT_ADAPTER_INBOUND_URL,
                   help="adapter inbound URL")
    p.add_argument("--cancel", action="store_true",
                   help="send a PCC-58 cancellation instead of a PCC-55 request")
    args = p.parse_args()
    if args.cancel:
        result = send_cancellation(args.referral, args.url)
    else:
        result = send_referral_request(args.referral, args.url)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _main()
