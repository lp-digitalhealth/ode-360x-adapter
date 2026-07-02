"""FastAPI app — the two faces of the 360/ODE Adapter.

  POST /360x/inbound     accept an inbound 360X message (via the configured codec)
  POST /ode/task-event   accept an ODE Native Task change, emit outbound 360X
  GET  /episodes         inspect the correlation store
  GET  /plugins          list available plugins (plug-and-play visibility)
  GET  /healthz

Inbound is decoded by the configured IheCodec; outbound is sent by the configured
IheOutboundTransport. Select plugins via env (ODE_ADAPTER_FHIR_BACKEND, _IHE_CODEC,
_IHE_TRANSPORT).
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import registry
from .config import settings
from .engine import Adapter

app = FastAPI(title="360/ODE Adapter (OHIA reference)", version="0.1.0")
adapter = Adapter.from_config()


class TaskEvent(BaseModel):
    task: dict[str, Any]
    result_resources: list[dict[str, Any]] = []
    patient: dict[str, Any] | None = None
    interim: bool = False


class ReferralInitiation(BaseModel):
    """Dental-initiated PCC-55: a referral intake -> outbound 360X Referral Request.

    Carries a referral-grade dataset: enough for the receiver to diagnose, treat,
    and bill, plus the referring + rendering provider identities.
    """
    referral_id: str
    # Referral direction — selects the ODE directional ServiceRequest profile and
    # its must-support. One of: medical-to-dental | dental-to-dental | dental-to-medical.
    direction: str | None = None
    priority: str | None = "routine"
    patient: dict[str, Any] | None = None
    coverage: dict[str, Any] | None = None
    referring_provider: dict[str, Any] | None = None
    rendering_provider: dict[str, Any] | None = None
    service: dict[str, Any] | None = None
    diagnoses: list[dict[str, Any]] = []
    medications: list[dict[str, Any]] = []
    reason_text: str | None = None
    supporting_info: str | None = None
    sender: str | None = None
    recipient: str | None = None


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/plugins")
def plugins() -> dict:
    return {"selected": {"fhir": settings.fhir_backend,
                         "codec": settings.ihe_codec,
                         "transport": settings.ihe_transport},
            "available": registry.all_plugins()}


@app.post("/360x/inbound")
def inbound_360x(envelope: dict[str, Any]) -> dict:
    try:
        return adapter.handle_inbound(envelope)
    except (KeyError, ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/ode/task-event")
def ode_task_event(event: TaskEvent) -> dict:
    try:
        return adapter.handle_task_event(
            event.task, result_resources=event.result_resources,
            patient=event.patient, interim=event.interim)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/ode/referral")
def ode_referral(init: ReferralInitiation) -> dict:
    try:
        return adapter.handle_referral_initiation(
            referral_id=init.referral_id, rich=init.model_dump(),
            sender=init.sender, recipient=init.recipient)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


class AppointmentEvent(BaseModel):
    referral_id: str
    no_show: bool = False
    appointment_start: str | None = None
    appointment_end: str | None = None
    location: str | None = None
    provider: str | None = None
    appt_type: str | None = None
    reason: str | None = None
    reschedule: str | None = None


@app.post("/ode/appointment-event")
def ode_appointment_event(event: AppointmentEvent) -> dict:
    try:
        return adapter.handle_appointment_event(
            referral_id=event.referral_id, no_show=event.no_show,
            appointment_start=event.appointment_start,
            appointment_end=event.appointment_end, location=event.location,
            provider=event.provider, appt_type=event.appt_type,
            reason=event.reason, reschedule=event.reschedule)
    except (ValueError, NotImplementedError) as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/episodes")
def episodes() -> dict:
    return {"episodes": adapter.store.all()}


@app.get("/episodes/{referral_id}/inbox")
def episode_inbox(referral_id: str) -> dict:
    """The FHIR a PMS/EHR would read for this referral. Live: query the FHIR server
    (find_by_referral); dry-run: the per-episode cache the bridge accumulated."""
    live = adapter.fhir.find_by_referral(referral_id)
    ep = adapter.store.get(referral_id)
    resources = live or (ep.inbox if ep else [])
    return {"referral_id": referral_id,
            "source": "fhir-server" if live else "episode-cache",
            "status": ep.status if ep else None,
            "business_status": ep.business_status if ep else None,
            "resources": resources}
