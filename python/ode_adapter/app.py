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


@app.get("/episodes")
def episodes() -> dict:
    return {"episodes": adapter.store.all()}
