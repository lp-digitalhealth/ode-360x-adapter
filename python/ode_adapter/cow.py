"""COW subset helpers — the scoped Clinical Order Workflows layer.

Everything here is *scoped to what 360X supports* (see
spec/mapping/360x-cow-crosswalk.md). It builds the COW/FHIR artifacts the bridge
applies on every reply: the dental `businessStatus`, `Task.output` wrapping, the
provisional dental profiles, and the non-document reply resources (Appointment from
PCC-60, Communication from PCC-61). Keeping it in one module makes the COW subset
auditable against the crosswalk.
"""
from __future__ import annotations

import uuid

from . import config


def _urn() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def business_status_concept(code: str) -> dict:
    """CodeableConcept for Task.businessStatus from the dental value set."""
    return {"coding": [{"system": config.COW_BUSINESS_STATUS_SYSTEM, "code": code,
                        "display": config.COW_BUSINESS_STATUS.get(code, code)}],
            "text": config.COW_BUSINESS_STATUS.get(code, code)}


def _concept(system: str, table: dict, code: str | None) -> dict | None:
    """CodeableConcept from a provisional value set; falls back to free text."""
    if not code:
        return None
    display = table.get(code)
    if display:
        return {"coding": [{"system": system, "code": code, "display": display}],
                "text": display}
    return {"text": code}   # unrecognized code -> carry the text (degraded)


def decline_reason_concept(code: str | None) -> dict | None:
    """Task.statusReason for a PCC-56 decline (coded reason)."""
    return _concept(config.DECLINE_REASON_SYSTEM, config.DECLINE_REASONS, code)


def appointment_type_concept(code: str | None) -> dict | None:
    return _concept(config.APPOINTMENT_TYPE_SYSTEM, config.APPOINTMENT_TYPES, code)


def noshow_reason_concept(code: str | None) -> dict | None:
    return _concept(config.NOSHOW_REASON_SYSTEM, config.NOSHOW_REASONS, code)


def clearance_concept(code: str | None) -> dict | None:
    return _concept(config.CLEARANCE_SYSTEM, config.CLEARANCE_DISPOSITIONS, code)


def build_owner_role(provider: dict | None) -> tuple[list[dict], str | None]:
    """Build the accepting provider's identity for Task.owner (PCC-56 accept).

    Returns (resources, owner_ref): the PractitionerRole (+ Practitioner + optional
    Organization) to write, and the PractitionerRole fullUrl to set as Task.owner.
    An empty provider yields ([], None) so accept still works without an identity.
    """
    provider = provider or {}
    if not any(provider.get(k) for k in ("name", "npi", "specialty", "organization")):
        return [], None
    pract = {"resourceType": "Practitioner", "_fullUrl": _urn(),
             "meta": {"profile": [
                 "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner"]},
             "name": [{"text": provider.get("name") or "Accepting Provider"}]}
    if provider.get("npi"):
        pract["identifier"] = [{"system": config.SYS_NPI, "value": provider["npi"]}]
    resources = [pract]
    org = None
    if provider.get("organization"):
        org = {"resourceType": "Organization", "_fullUrl": _urn(),
               "name": provider["organization"],
               "meta": {"profile": [
                   "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization"]}}
        resources.append(org)
    role = {"resourceType": "PractitionerRole", "_fullUrl": _urn(),
            "practitioner": {"reference": pract["_fullUrl"]}}
    if org:
        role["organization"] = {"reference": org["_fullUrl"]}
    if provider.get("specialty"):
        role["specialty"] = [{"coding": [{"system": config.SYS_NUCC,
                                          "display": provider["specialty"]}],
                              "text": provider["specialty"]}]
    resources.append(role)
    return resources, role["_fullUrl"]


def interim_observation(referral_id: str, patient_ref: str | None,
                        finding: str, value: str | None = None) -> dict:
    """A single interim finding Observation for PCC-59 (in addition to the note)."""
    obs = {"resourceType": "Observation", "_fullUrl": _urn(),
           "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
           "status": "preliminary",
           "code": {"text": finding or "Interim finding"}}
    if patient_ref:
        obs["subject"] = {"reference": patient_ref}
    if value:
        obs["valueString"] = value
    return obs


def clearance_observation(referral_id: str, patient_ref: str | None,
                          disposition: str) -> dict:
    """A coded clearance/disposition Observation for PCC-57 outcome."""
    concept = clearance_concept(disposition) or {"text": disposition}
    obs = {"resourceType": "Observation", "_fullUrl": _urn(),
           "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
           "status": "final",
           "code": {"coding": [{"system": config.SYS_LOINC,
                                "code": config.CLEARANCE_LOINC,
                                "display": "Referral clearance / disposition"}],
                    "text": "Referral clearance / disposition"},
           "valueCodeableConcept": concept}
    if patient_ref:
        obs["subject"] = {"reference": patient_ref}
    return obs


def build_medication_list(referral_id: str, patient_ref: str | None,
                          med_urls: list[str]) -> dict:
    """The ODEMedicationList (`List`) aggregating the patient's current medications.

    Per the ODE contract this is a base-FHIR `List` (status=current, mode=snapshot,
    code=LOINC 10160-0) whose entries reference the US Core MedicationRequests. It
    travels inside the referral Bundle and is referenced from ServiceRequest.supportingInfo.
    """
    lst = {"resourceType": "List", "_fullUrl": _urn(),
           "meta": {"profile": [config.PROFILE_ODE_MEDICATION_LIST]},
           "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
           "status": "current", "mode": "snapshot",
           "title": "Medication List",
           "code": {"coding": [{"system": config.SYS_LOINC,
                                "code": config.MED_LIST_LOINC,
                                "display": "History of Medication use Narrative"}],
                    "text": "Medication List"},
           "entry": [{"item": {"reference": u}} for u in med_urls]}
    if patient_ref:
        lst["subject"] = {"reference": patient_ref}
    return lst


def task_output(resources: list[dict]) -> list[dict]:
    """Wrap result resources as Task.output entries (reference by fullUrl/id)."""
    out: list[dict] = []
    for res in resources or []:
        ref = res.get("_fullUrl") or (
            f"{res['resourceType']}/{res['id']}" if res.get("id") else None)
        if not ref:
            continue
        out.append({
            "type": {"text": res["resourceType"]},
            "valueReference": {"reference": ref},
        })
    return out


def apply_dental_profiles(resources: list[dict]) -> None:
    """Stamp provisional ODE dental profiles on dental-flavored resources in place."""
    for res in resources or []:
        rtype = res.get("resourceType")
        if rtype == "Procedure" and _is_cdt(res):
            _add_profile(res, config.PROFILE_DENTAL_PROCEDURE)
        elif rtype == "Observation" and (res.get("_dental_perio")
                                         or _profile_has(res, "perio")):
            _add_profile(res, config.PROFILE_PERIO_OBSERVATION)


def build_appointment(referral_id: str, patient_ref: str | None,
                      task_ref: str | None, start: str | None,
                      *, status: str = "booked", end: str | None = None,
                      location: str | None = None, provider: str | None = None,
                      appt_type: str | None = None) -> dict:
    """COW reply resource for PCC-60 Appointment Notification.

    Carries the scheduling detail a fulfiller sends: start/end, the booking
    provider (participant), a description (location) and appointmentType.
    """
    appt = {"resourceType": "Appointment", "_fullUrl": _urn(),
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
            "status": status}
    if start:
        appt["start"] = _iso(start)
    if end:
        appt["end"] = _iso(end)
    type_cc = appointment_type_concept(appt_type)
    if type_cc:
        appt["appointmentType"] = type_cc
    if location:
        appt["description"] = location
    parts = []
    if patient_ref:
        parts.append({"actor": {"reference": patient_ref}, "status": "accepted"})
    if provider:
        parts.append({"actor": {"display": provider}, "status": "accepted"})
    if parts:
        appt["participant"] = parts
    if task_ref:
        appt["basedOn"] = [{"reference": task_ref}]
    return appt


def build_communication(referral_id: str, patient_ref: str | None,
                        task_ref: str | None, *, reason: str,
                        reason_code: str | None = None,
                        reschedule: str | None = None) -> dict:
    """COW reply resource for PCC-61 No-Show Notification.

    `reason_code` (optional) is a coded no-show reason; `reschedule` is a free-text
    reschedule instruction carried in the payload.
    """
    payloads = [{"contentString": reason}]
    if reschedule:
        payloads.append({"contentString": f"Reschedule: {reschedule}"})
    comm = {"resourceType": "Communication", "_fullUrl": _urn(),
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
            "status": "completed",
            "reasonCode": [noshow_reason_concept(reason_code) or {"text": reason}],
            "payload": payloads}
    if patient_ref:
        comm["subject"] = {"reference": patient_ref}
    if task_ref:
        comm["partOf"] = [{"reference": task_ref}]
    return comm


def task_snapshot(referral_id: str, task_id: str | None, status: str,
                  business_status: str, *, focus_ref: str | None = None,
                  patient_ref: str | None = None,
                  outputs: list[dict] | None = None,
                  owner: str | dict | None = None,
                  status_reason: dict | None = None,
                  note: str | None = None,
                  period_end: str | None = None) -> dict:
    """A COW Task resource snapshot for the harness inbox / export (what the PMS
    would read from the FHIR server after the bridge applied a reply). Carries the
    reply content (owner/statusReason/note/restriction) when supplied."""
    task = {"resourceType": "Task",
            "meta": {"profile": [config.PROFILE_COW_TASK]},
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
            "status": status, "intent": "order",
            "businessStatus": business_status_concept(business_status),
            "code": {"coding": [{
                "system": "http://hl7.org/fhir/CodeSystem/task-code",
                "code": "fulfill"}]}}
    if task_id:
        task["id"] = task_id
    if focus_ref:
        task["focus"] = {"reference": focus_ref}
    if patient_ref:
        task["for"] = {"reference": patient_ref}
    if owner:
        task["owner"] = {"reference": owner} if isinstance(owner, str) else owner
    if status_reason:
        task["statusReason"] = status_reason
    if note:
        task["note"] = [{"text": note}]
    if period_end:
        task["restriction"] = {"period": {"end": period_end}}
    if outputs:
        task["output"] = outputs
    return task


# --------------------------------------------------------------------------- #
def _iso(value: str) -> str:
    """Accept an HL7 v2 timestamp (YYYYMMDDHHMMSS) or pass through ISO-8601."""
    v = (value or "").strip()
    if v.isdigit() and len(v) >= 8:
        out = f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
        if len(v) >= 12:
            out += f"T{v[8:10]}:{v[10:12]}:{v[12:14] or '00'}Z"
        return out
    return v


def _is_cdt(res: dict) -> bool:
    for c in (res.get("code", {}) or {}).get("coding", []):
        if c.get("system") == config.SYS_CDT:
            return True
    return False


def _profile_has(res: dict, needle: str) -> bool:
    return any(needle in p.lower()
               for p in (res.get("meta") or {}).get("profile", []))


def _add_profile(res: dict, profile: str) -> None:
    meta = res.setdefault("meta", {})
    profs = meta.setdefault("profile", [])
    if profile not in profs:
        profs.append(profile)
