"""Stub 360X server — stands in for the medical-side Referral Initiator endpoint.

Thin FastAPI wrapper over `stub_360x_core.Stub360XReceiver` (the receive logic lives
there so it can be unit-tested without HTTP). Stands in for a Direct/HISP + XDM
receiver over plain HTTP so the adapter's outbound path can be exercised:

    adapter  --(http transport)-->  POST /360x/receive  -->  this stub

Run it (from the python/ directory):
    pip install -e ".[server,fhir]"
    uvicorn tools.stub_360x_server:app --port 9000

Point the adapter at it:
    ODE_ADAPTER_IHE_TRANSPORT=http
    ODE_ADAPTER_IHE_OUTBOUND_URL=http://localhost:9000/360x/receive

It does NOT implement Direct/SMTP, S/MIME, or real XDM — that is the production
`direct` transport's job.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .stub_360x_core import Stub360XReceiver, EnvelopeError, KNOWN_TRANSACTIONS
from . import stub_360x_sender as sender

app = FastAPI(title="Stub 360X server (OHIA reference receiver)", version="0.1.0")
receiver = Stub360XReceiver()


class Document(BaseModel):
    id: str
    mime_type: str = "text/xml"
    content: str = ""


class Envelope(BaseModel):
    """Matches OutboundEnvelope.to_dict() from the adapter."""
    transaction: str
    sender_direct_address: str = ""
    recipient_direct_address: str = ""
    hl7v2: str = ""
    documents: list[Document] = Field(default_factory=list)


@app.get("/healthz")
def healthz() -> dict:
    return receiver.health()


@app.post("/360x/receive")
def receive(envelope: Envelope) -> dict:
    try:
        return receiver.receive(envelope.model_dump())
    except EnvelopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/360x/received")
def received(transaction: str | None = None) -> dict:
    items = receiver.received(transaction)
    return {"count": len(items), "items": items}


@app.delete("/360x/received")
def clear() -> dict:
    return {"cleared": receiver.clear()}


@app.get("/360x/transactions")
def transactions() -> dict:
    return {"known": KNOWN_TRANSACTIONS}


# --- Sending: the stub acts as the medical-side Referral Initiator ----------- #
class SendReferral(BaseModel):
    referral_id: str = "REF-1001"
    adapter_inbound_url: str | None = None
    sender_direct_address: str | None = None
    recipient_direct_address: str | None = None


class SendCancellation(BaseModel):
    referral_id: str
    adapter_inbound_url: str | None = None


def _addr_kwargs(body) -> dict:
    kw = {}
    if getattr(body, "sender_direct_address", None):
        kw["sender_direct_address"] = body.sender_direct_address
    if getattr(body, "recipient_direct_address", None):
        kw["recipient_direct_address"] = body.recipient_direct_address
    return kw


@app.post("/360x/send-referral")
def send_referral(body: SendReferral) -> dict:
    """Build a PCC-55 referral and POST it to the adapter's inbound endpoint."""
    url = body.adapter_inbound_url or sender.DEFAULT_ADAPTER_INBOUND_URL
    envelope = sender.build_referral_request(body.referral_id, **_addr_kwargs(body))
    try:
        adapter_response = sender.send_envelope(envelope, url)
    except Exception as exc:  # noqa: BLE001  (network/HTTP errors -> 502)
        raise HTTPException(status_code=502,
                            detail=f"could not reach adapter at {url}: {exc}")
    return {"sent": "PCC-55", "referral_id": body.referral_id, "to": url,
            "adapter_response": adapter_response}


@app.post("/360x/send-cancellation")
def send_cancellation(body: SendCancellation) -> dict:
    """Build a PCC-58 cancellation and POST it to the adapter's inbound endpoint."""
    url = body.adapter_inbound_url or sender.DEFAULT_ADAPTER_INBOUND_URL
    envelope = sender.build_cancellation(body.referral_id)
    try:
        adapter_response = sender.send_envelope(envelope, url)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502,
                            detail=f"could not reach adapter at {url}: {exc}")
    return {"sent": "PCC-58", "referral_id": body.referral_id, "to": url,
            "adapter_response": adapter_response}
