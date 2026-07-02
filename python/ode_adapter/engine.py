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

from . import (ccda_to_fhir, cow, fhir_to_ccda, hl7v2, referral_fhir, registry,
               state_machine)
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
        if env.transaction in state_machine.REPLY_TRANSACTIONS:
            return self._inbound_reply(env, referral_id, v2)
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
            task_id=task_id, service_request_id=sr_id, status="requested",
            business_status="referral-sent", initiated_by="medical"))
        # Cache the referral FHIR so the receiving side's inbox shows the full
        # diagnose/treat/bill dataset the C-CDA carried (providers, coverage, dx...).
        ep.add_to_inbox(_produced(bundle))
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
        ep.business_status = "cancelled"
        ep.note("PCC-58 received; Task revoked (cancelled)")
        return {"referral_id": referral_id, "task_id": ep.task_id,
                "action": "task_cancelled"}

    # -------------------- INBOUND: reply ingestion (mirror) --------------- #
    def _inbound_reply(self, env: InboundEnvelope, referral_id: str,
                       v2) -> dict:
        """Project a peer's reply 360X transaction (PCC-56/57/59/60/61) onto the COW
        Task/Request state, write the resulting FHIR, and cache it for the harness
        inbox. Direction-agnostic: used whenever *this* side initiated the referral.
        """
        tx = env.transaction
        decision = state_machine.reply_to_fhir(
            tx, v2.order_status if v2 else None)
        if not decision:
            raise ValueError(f"Unsupported reply transaction: {tx}")

        ep = self.store.get(referral_id) or self._orphan_episode(env, referral_id)
        patient_ref = None  # correlation-only; resources reference by referral id
        task_ref = f"Task/{ep.task_id}" if ep.task_id else None

        fields = (v2.fields if v2 else {})
        produced: list[dict] = []
        outputs: list[dict] = []
        cda = env.primary_cda()
        # Reply-content extras -> the (lossless) FHIR Task update. The 360X side
        # carried these degraded in NTE/ORC-12/ORC-15/ORC-16 (see hl7v2.parse).
        note_text = fields.get("note")
        owner_ref = None
        status_reason_cc = None
        period_end = None

        if decision.kind in ("outcome", "interim"):
            if not cda:
                raise ValueError(f"{tx} missing C-CDA Consultation Note")
            bundle = ccda_to_fhir.transform_consultation_note(
                cda, referral_id=referral_id, interim=(decision.kind == "interim"))
            _stamp_provenance(bundle)
            response = self.fhir.submit_referral_bundle(bundle)
            produced = [e["resource"] for e in bundle.get("entry", [])
                        if e["resource"]["resourceType"] != "Provenance"]
            cow.apply_dental_profiles(produced)
            # A coded reply resource beyond the parsed note: an interim finding, or
            # the final clearance/disposition (carried degraded in NTE-3).
            pref = _patient_ref(produced)
            if decision.kind == "interim":
                produced.append(cow.interim_observation(
                    referral_id, pref, note_text or "Interim finding"))
            else:
                produced.append(cow.clearance_observation(
                    referral_id, pref, note_text or "cleared"))
            outputs = cow.task_output(produced)
        elif decision.kind == "appointment":
            appt = cow.build_appointment(
                referral_id, patient_ref, task_ref,
                fields.get("appointment_start"), end=fields.get("appointment_end"),
                location=note_text, provider=fields.get("accepting_provider"),
                appt_type=fields.get("status_reason"))
            produced = [appt]
            self.fhir.submit_referral_bundle(_wrap_bundle(produced))
            outputs = cow.task_output(produced)
        elif decision.kind == "noshow":
            comm = cow.build_communication(
                referral_id, patient_ref, task_ref,
                reason="Patient did not attend the scheduled appointment (no-show).",
                reason_code=fields.get("status_reason"), reschedule=note_text)
            produced = [comm]
            self.fhir.submit_referral_bundle(_wrap_bundle(produced))
            outputs = cow.task_output(produced)
        elif decision.kind == "status":
            if tx == "PCC-56" and decision.task_status == "accepted":
                owner_res, owner_ref = cow.build_owner_role(
                    _provider_from_v2(fields.get("accepting_provider")))
                if owner_res:
                    self.fhir.submit_referral_bundle(_wrap_bundle(owner_res))
                    produced.extend(owner_res)
                period_end = _iso_date(fields.get("period_end"))
            elif tx == "PCC-56":   # decline
                status_reason_cc = cow.decline_reason_concept(
                    fields.get("status_reason"))

        # Apply the COW Task state change (status + businessStatus + content + output).
        if ep.task_id:
            task_snapshot = self.fhir.update_task_status(
                ep.task_id, decision.task_status,
                reason=f"{tx} received",
                business_status=decision.business_status,
                outputs=outputs or None,
                owner=owner_ref, status_reason=status_reason_cc,
                note=note_text, period_end=period_end)
        else:
            task_snapshot = cow.task_snapshot(
                referral_id, None, decision.task_status, decision.business_status,
                outputs=outputs or None, owner=owner_ref,
                status_reason=status_reason_cc, note=note_text,
                period_end=period_end)

        # Apply the COW Request lifecycle change when it moves.
        if decision.request_status in ("completed", "revoked") and ep.service_request_id:
            self.fhir.update_request_status(
                ep.service_request_id, decision.request_status,
                reason=f"{tx} received")

        ep.status = decision.task_status
        ep.business_status = decision.business_status
        ep.note(f"{tx} received; Task -> {decision.task_status} "
                f"({decision.business_status})")
        ep.add_to_inbox(produced + ([task_snapshot] if task_snapshot else []))

        return {"referral_id": referral_id, "transaction": tx,
                "task_status": decision.task_status,
                "business_status": decision.business_status,
                "request_status": decision.request_status,
                "task": task_snapshot, "resources": produced,
                "task_id": ep.task_id}

    def _orphan_episode(self, env: InboundEnvelope, referral_id: str) -> Episode:
        """A reply arrived without a tracked initiation (harness simulates a reply in
        isolation). Open a minimal episode so the loop still correlates."""
        ep = self.store.create(Episode(
            referral_id=referral_id,
            direct_message_id=env.direct_message_id,
            submission_set_id=env.submission_set_id,
            sender_direct_address=env.sender_direct_address,
            recipient_direct_address=env.recipient_direct_address,
            status="requested"))
        ep.note("reply received without a tracked initiation; episode opened")
        return ep

    # ---------------- OUTBOUND: referral initiation (dental) -------------- #
    def handle_referral_initiation(self, *, referral_id: str,
                                   rich: dict | None = None,
                                   patient: dict | None = None,
                                   service_request: dict | None = None,
                                   conditions: list[dict] | None = None,
                                   reason_text: str | None = None,
                                   sender: str | None = None,
                                   recipient: str | None = None) -> dict:
        """Dental-initiated direction: a FHIR referral becomes a 360X PCC-55 Referral
        Request (Referral Note C-CDA) emitted to the medical peer. Mirror of the
        inbound PCC-55 path. Opens the correlation episode from this side.

        Two shapes are accepted:
          * `rich` — the full referral intake (patient/coverage/referring +
            rendering providers/coded diagnoses/requested service/priority/clinical
            justification). Builds the rich ODE Native FHIR referral (enough to
            diagnose, treat, and bill) AND the degraded 360X C-CDA.
          * legacy keyword resources (`patient`, `service_request`, ...) — kept for
            back-compat; C-CDA only.
        """
        produced: list[dict] = []
        bundle = None
        if rich is not None:
            rich = {**rich, "referral_id": referral_id}
            bundle = referral_fhir.build_referral_bundle(rich)
            _stamp_provenance(bundle)
            self.fhir.submit_referral_bundle(bundle)
            produced = _produced(bundle)
            cda, loss_notes = fhir_to_ccda.build_referral_note(rich=rich)
            sender = sender or rich.get("sender")
            recipient = recipient or rich.get("recipient")
            sr_id = _sr_id(produced)
        else:
            cda, loss_notes = fhir_to_ccda.build_referral_note(
                patient=patient, service_request=service_request,
                conditions=conditions, reason_text=reason_text)
            sr_id = service_request.get("id") if service_request else None

        v2 = hl7v2.build("OMG^O19", referral_id=referral_id)
        documents = [XdmDocument(id=f"{referral_id}-PCC-55", mime_type="text/xml",
                                 content=cda)]
        env = OutboundEnvelope(
            transaction="PCC-55",
            sender_direct_address=sender or "intake@dentalgroup.direct.example.org",
            recipient_direct_address=recipient or "referrals@oncology.direct.example.org",
            hl7v2=v2, documents=documents)
        packaged = self.codec.pack(env)
        self.outbound.send(packaged)

        ep = self.store.get(referral_id) or self.store.create(Episode(
            referral_id=referral_id,
            direct_message_id=f"<{referral_id}@dental>",
            submission_set_id=f"urn:ohia:submissionset:{referral_id}",
            sender_direct_address=env.sender_direct_address,
            recipient_direct_address=env.recipient_direct_address,
            service_request_id=sr_id, status="requested",
            business_status="referral-sent", initiated_by="dental"))
        if produced:
            ep.add_to_inbox(produced)
        ep.note("PCC-55 initiated (dental -> medical); referral sent")
        return {"referral_id": referral_id, "transaction": "PCC-55",
                "packaged": packaged, "loss_notes": loss_notes,
                "bundle": bundle, "resources": produced,
                "service_request_id": sr_id}

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

        # Carry the reply content the fulfiller put on the Task into the (degraded)
        # 360X v2: accepting provider (ORC-12), expected-by (ORC-15), decline reason
        # (ORC-16), acknowledgment/comment (NTE).
        v2 = hl7v2.build(hl7v2.TX_MESSAGE_TYPE[decision.transaction],
                         referral_id=referral_id, order_status=decision.order_status,
                         note=_task_note(task),
                         accepting_provider=_task_owner_provider(task),
                         status_reason=_task_status_reason_code(task),
                         period_end=_task_period_end(task))

        env = OutboundEnvelope(
            transaction=decision.transaction,
            sender_direct_address=(ep.recipient_direct_address if ep
                                   else "dental@example.org"),
            recipient_direct_address=(ep.sender_direct_address if ep
                                      else "ehr@example.org"),
            hl7v2=v2, documents=documents)

        packaged = self.codec.pack(env)
        self.outbound.send(packaged)

        return self._finish_outbound(ep, referral_id, decision, packaged, status,
                                     loss_notes)

    def handle_appointment_event(self, *, referral_id: str, no_show: bool = False,
                                 appointment_start: str | None = None,
                                 appointment_end: str | None = None,
                                 location: str | None = None,
                                 provider: str | None = None,
                                 appt_type: str | None = None,
                                 reason: str | None = None,
                                 reschedule: str | None = None) -> dict:
        """Outbound scheduling: emit PCC-60 Appointment / PCC-61 No-Show (SIU). The
        mirror of the inbound appointment/noshow reply ingestion. Scheduling detail
        (end/location/provider/type) and no-show reason/reschedule ride degraded on
        the v2 side (SCH-12, ORC-12, ORC-16, NTE)."""
        ep = self.store.get(referral_id)
        decision = state_machine.appointment_event(no_show=no_show)
        if no_show:
            v2 = hl7v2.build(hl7v2.TX_MESSAGE_TYPE[decision.transaction],
                             referral_id=referral_id,
                             note=reschedule or reason, status_reason=reason)
        else:
            v2 = hl7v2.build(hl7v2.TX_MESSAGE_TYPE[decision.transaction],
                             referral_id=referral_id,
                             appointment_start=appointment_start,
                             appointment_end=appointment_end,
                             note=location, accepting_provider=provider,
                             status_reason=appt_type)
        env = OutboundEnvelope(
            transaction=decision.transaction,
            sender_direct_address=(ep.recipient_direct_address if ep
                                   else "dental@example.org"),
            recipient_direct_address=(ep.sender_direct_address if ep
                                      else "ehr@example.org"),
            hl7v2=v2, documents=[])
        packaged = self.codec.pack(env)
        self.outbound.send(packaged)
        return self._finish_outbound(ep, referral_id, decision, packaged,
                                     status=None, loss_notes=[])

    def _finish_outbound(self, ep, referral_id, decision, packaged, status,
                         loss_notes) -> dict:
        business = _OUTBOUND_BUSINESS_STATUS.get(decision.transaction)
        if decision.transaction == "PCC-56" and decision.order_status == "CA":
            business = "declined"
        if ep:
            if status:
                ep.status = status
            if business:
                ep.business_status = business
            ep.note(f"{decision.transaction} emitted (status={status}, "
                    f"businessStatus={business})")
        return {"referral_id": referral_id, "transaction": decision.transaction,
                "business_status": business,
                "packaged": packaged, "loss_notes": loss_notes}


# COW business-status applied when emitting each outbound reply (medical-initiated).
_OUTBOUND_BUSINESS_STATUS = {
    "PCC-56": "accepted",       # decline is overridden to "declined" at the call site
    "PCC-59": "interim-results",
    "PCC-57": "outcome-final",
    "PCC-58": "cancelled",
    "PCC-60": "appointment-booked",
    "PCC-61": "appointment-noshow",
}


# --------------------------------------------------------------------------- #
def _wrap_bundle(resources: list[dict]) -> dict:
    """Wrap plain resources as a FHIR transaction Bundle (POST each)."""
    entries = []
    for res in resources:
        clean = {k: v for k, v in res.items() if not k.startswith("_")}
        entries.append({"fullUrl": res.get("_fullUrl"),
                        "resource": clean,
                        "request": {"method": "POST",
                                    "url": clean["resourceType"]}})
    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _produced(bundle: dict) -> list[dict]:
    """The non-Provenance resources a bundle wrote (for the harness inbox)."""
    return [e["resource"] for e in bundle.get("entry", [])
            if e.get("resource", {}).get("resourceType") != "Provenance"]


def _sr_id(resources: list[dict]) -> str | None:
    for res in resources:
        if res.get("resourceType") == "ServiceRequest":
            return res.get("id")
    return None


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


def _patient_ref(resources: list[dict]) -> str | None:
    for res in resources:
        if res.get("resourceType") == "Patient":
            return res.get("_fullUrl") or (
                f"Patient/{res['id']}" if res.get("id") else None)
    return None


def _provider_from_v2(field: str | None) -> dict | None:
    """Parse an ORC-12 provider field ('NPI^Name' or 'Name') into a provider dict."""
    if not field:
        return None
    parts = field.split("^")
    if len(parts) >= 2 and parts[0]:
        return {"npi": parts[0], "name": "^".join(parts[1:]).strip() or None}
    return {"name": field}


def _iso_date(v: str | None) -> str | None:
    """HL7 v2 date (YYYYMMDD) -> ISO date; pass ISO through."""
    if not v:
        return None
    v = v.strip()
    if v.isdigit() and len(v) >= 8:
        return f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
    return v


def _task_note(task: dict) -> str | None:
    notes = task.get("note") or []
    if notes and isinstance(notes[0], dict):
        return notes[0].get("text")
    return None


def _task_owner_provider(task: dict) -> str | None:
    """Task.owner -> an ORC-12 'NPI^Name' string (degraded)."""
    owner = task.get("owner") or {}
    npi = ""
    for ident in owner.get("identifier", []) if isinstance(owner, dict) else []:
        if ident.get("system") == "http://hl7.org/fhir/sid/us-npi":
            npi = ident.get("value", "")
    name = owner.get("display") if isinstance(owner, dict) else None
    if npi or name:
        return f"{npi}^{name or ''}".strip("^") or None
    return None


def _task_status_reason_code(task: dict) -> str | None:
    sr = task.get("statusReason") or {}
    for c in sr.get("coding", []):
        if c.get("code"):
            return c["code"]
    return sr.get("text") or None


def _task_period_end(task: dict) -> str | None:
    end = ((task.get("restriction") or {}).get("period") or {}).get("end")
    if not end:
        return None
    return end.replace("-", "")[:8] or None
