"""Engine — orchestrates the three layers via the ports (plug and play).

The engine depends only on the FhirBackend / IheCodec / IheOutboundTransport ports.
Concrete plugins are injected (or selected from config via `Adapter.from_config`).

Inbound (medical 360X -> ODE Native):
    raw -> codec.unpack -> parse v2 + C-CDA -> FHIR Bundle -> fhir.submit -> correlate.

Outbound (ODE Native Task change -> medical 360X):
    Task (+results) -> 360X decision -> v2 (+C-CDA) -> codec.pack -> transport.send.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import ccda_to_fhir, fhir_to_ccda, hl7v2, registry, state_machine
from .config import settings
from .ports import FhirBackend, IheCodec, IheOutboundTransport
from .stores import CorrelationStore, Directory, Episode
from .xdm import InboundEnvelope, OutboundEnvelope, XdmDocument


class Adapter:
    def __init__(self, fhir: FhirBackend, codec: IheCodec,
                 outbound: IheOutboundTransport,
                 store: CorrelationStore | None = None,
                 directory: Directory | None = None):
        self.fhir = fhir
        self.codec = codec
        self.outbound = outbound
        self.store = store or CorrelationStore()
        self.directory = directory or Directory()

    @classmethod
    def from_config(cls) -> "Adapter":
        """Build an adapter from config-selected plugins."""
        import ode_adapter.plugins  # noqa: F401  (registers built-ins)
        fhir = registry.create("fhir", settings.fhir_backend,
                               base_url=settings.ode_native_base_url,
                               dry_run=settings.dry_run)
        codec = registry.create("codec", settings.ihe_codec)
        outbound = registry.create("transport", settings.ihe_transport)
        return cls(fhir=fhir, codec=codec, outbound=outbound)

    # ----------------------------- INBOUND ------------------------------- #
    def handle_inbound(self, raw: bytes | str | dict) -> dict:
        env = self.codec.unpack(raw)
        v2 = hl7v2.parse(env.hl7v2) if env.hl7v2 else None
        referral_id = (v2.referral_id if v2 and v2.referral_id
                       else env.submission_set_id)

        if env.transaction == "PCC-55":
            return self._inbound_referral_request(env, referral_id)
        if env.transaction == "PCC-58":
            return self._inbound_cancellation(referral_id)
        raise ValueError(f"Unsupported inbound transaction: {env.transaction}")

    def _inbound_referral_request(self, env: InboundEnvelope, referral_id: str) -> dict:
        cda = env.primary_cda()
        if not cda:
            raise ValueError("PCC-55 missing C-CDA Referral Note")
        bundle = ccda_to_fhir.transform_referral_note(cda, referral_id=referral_id)
        _stamp_provenance(bundle)

        result = self.fhir.submit_referral_bundle(bundle)
        task_id, sr_id = _ids_from_response(result)

        ep = self.store.create(Episode(
            referral_id=referral_id,
            direct_message_id=env.direct_message_id,
            submission_set_id=env.submission_set_id,
            sender_direct_address=env.sender_direct_address,
            recipient_direct_address=env.recipient_direct_address,
            task_id=task_id, service_request_id=sr_id, status="requested"))
        ep.note(f"PCC-55 received; Task {task_id} created (requested)")
        return {"referral_id": referral_id, "task_id": task_id,
                "service_request_id": sr_id, "bundle": bundle, "ode_response": result}

    def _inbound_cancellation(self, referral_id: str) -> dict:
        ep = self.store.get(referral_id)
        if not ep:
            return {"referral_id": referral_id, "action": "cancellation_no_episode"}
        if ep.task_id:
            self.fhir.update_task_status(ep.task_id, "cancelled",
                                         reason="Referral cancelled by initiator (PCC-58)")
        ep.status = "cancelled"
        ep.note("PCC-58 received; Task revoked (cancelled)")
        return {"referral_id": referral_id, "task_id": ep.task_id,
                "action": "task_cancelled"}

    # ----------------------------- OUTBOUND ------------------------------ #
    def handle_task_event(self, task: dict, *,
                          result_resources: list[dict] | None = None,
                          patient: dict | None = None, interim: bool = False) -> dict:
        referral_id = _referral_id_from_task(task)
        ep = self.store.get(referral_id)
        status = task.get("status", "")

        decision = state_machine.task_to_360x(
            status, has_result=bool(result_resources), interim=interim)
        if not decision:
            return {"referral_id": referral_id, "action": "no_outbound_for_status",
                    "status": status}

        documents: list[XdmDocument] = []
        loss_notes: list[str] = []
        if decision.needs_document:
            cda, loss_notes = fhir_to_ccda.build_consultation_note(
                patient=patient, result_resources=result_resources or [],
                interim=interim)
            documents.append(XdmDocument(
                id=f"{referral_id}-{decision.transaction}", mime_type="text/xml",
                content=cda))

        v2 = hl7v2.build(hl7v2.TX_MESSAGE_TYPE[decision.transaction],
                         referral_id=referral_id, order_status=decision.order_status)

        env = OutboundEnvelope(
            transaction=decision.transaction,
            sender_direct_address=(ep.recipient_direct_address if ep
                                   else "dental@example.org"),
            recipient_direct_address=(ep.sender_direct_address if ep
                                      else "ehr@example.org"),
            hl7v2=v2, documents=documents)

        packaged = self.codec.pack(env)
        self.outbound.send(packaged)

        if ep:
            ep.status = status
            ep.note(f"{decision.transaction} emitted (status={status})")
        return {"referral_id": referral_id, "transaction": decision.transaction,
                "packaged": packaged, "loss_notes": loss_notes}


# --------------------------------------------------------------------------- #
def _stamp_provenance(bundle: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for e in bundle.get("entry", []):
        if e["resource"].get("resourceType") == "Provenance":
            e["resource"]["recorded"] = now


def _ids_from_response(response: dict) -> tuple[str | None, str | None]:
    task_id = sr_id = None
    for e in response.get("entry", []):
        res = e.get("resource", {})
        if res.get("resourceType") == "Task":
            task_id = res.get("id")
        elif res.get("resourceType") == "ServiceRequest":
            sr_id = res.get("id")
    return task_id, sr_id


def _referral_id_from_task(task: dict) -> str:
    for ident in task.get("identifier", []):
        if ident.get("system") == "urn:ohia:referral-id":
            return ident["value"]
    return task.get("id", "unknown")
