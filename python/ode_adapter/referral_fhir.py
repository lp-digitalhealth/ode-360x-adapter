"""Structured referral -> rich FHIR transaction Bundle.

A referral has to carry enough for the receiver to **diagnose, treat, and bill**.
This module turns the harness's structured referral intake (patient, coverage,
referring + rendering providers, coded diagnoses, requested service, priority,
clinical justification) into the FHIR resources that express all of that:

    Patient, Coverage (+ payer Organization), Practitioner/PractitionerRole/
    Organization for BOTH the referring (requester) and rendering (performer)
    providers, Condition(s) with ICD-10-CM/SNOMED, a ServiceRequest that ties them
    together (code = requested service, priority, reasonCode/reasonReference =
    diagnoses, requester, performer, insurance = Coverage, note = justification),
    a COW Task, and a Provenance.

This is the modern ODE Native representation; the degraded 360X C-CDA form of the same
referral is built in fhir_to_ccda.build_referral_note. Both consume the same input
dict shape (see `_get`), so the two directions stay in step.
"""
from __future__ import annotations

import uuid

from . import config, cow

PRIORITY_VALUES = {"routine", "urgent", "asap", "stat"}


def _urn() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _entry(resource: dict, rtype: str | None = None) -> dict:
    rtype = rtype or resource["resourceType"]
    return {"fullUrl": resource["_fullUrl"],
            "resource": {k: v for k, v in resource.items() if not k.startswith("_")},
            "request": {"method": "POST", "url": rtype}}


def _codeable(code: str | None, system: str, display: str | None) -> dict | None:
    if not code and not display:
        return None
    cc: dict = {}
    if code:
        cc["coding"] = [{"system": system, "code": code, "display": display}]
    cc["text"] = display or code
    return cc


def build_referral_bundle(rich: dict) -> dict:
    """Return a FHIR transaction Bundle for a structured referral intake."""
    referral_id = rich.get("referral_id") or "REF-UNKNOWN"
    direction = (rich.get("direction") or config.DEFAULT_DIRECTION).lower()
    entries: list[dict] = []

    patient = _build_patient(rich.get("patient") or {})
    entries.append(_entry(patient))

    # Referring (requester) and rendering (performer) providers.
    ref_role = ref_pract = ref_org = None
    referring = rich.get("referring_provider") or {}
    if referring:
        ref_pract, ref_org, ref_role = _build_provider_role(referring)
        for r in (ref_pract, ref_org, ref_role):
            if r:
                entries.append(_entry(r))

    rnd_role = rnd_pract = rnd_org = None
    rendering = rich.get("rendering_provider") or {}
    if rendering:
        rnd_pract, rnd_org, rnd_role = _build_provider_role(rendering)
        for r in (rnd_pract, rnd_org, rnd_role):
            if r:
                entries.append(_entry(r))

    # Coverage (billing).
    coverage = None
    cov_in = rich.get("coverage") or {}
    if cov_in.get("payer") or cov_in.get("member_id"):
        payer_org = _build_payer_org(cov_in)
        entries.append(_entry(payer_org))
        coverage = _build_coverage(cov_in, patient["_fullUrl"], payer_org["_fullUrl"])
        entries.append(_entry(coverage))

    # Diagnoses.
    conditions: list[dict] = []
    for dx in (rich.get("diagnoses") or []):
        cond = _build_condition(dx, patient["_fullUrl"])
        if cond:
            conditions.append(cond)
            entries.append(_entry(cond))

    # Current medication list (supporting clinical context for the order).
    medications: list[dict] = []
    for med in (rich.get("medications") or []):
        mr = _build_medication(med, patient["_fullUrl"])
        if mr:
            medications.append(mr)
            entries.append(_entry(mr))

    # Aggregate the meds into the ODEMedicationList (List) the contract expects; the
    # ServiceRequest references the List (not each MedicationRequest) in supportingInfo.
    support_urls: list[str] = []
    if medications:
        med_list = cow.build_medication_list(
            referral_id, patient["_fullUrl"], [m["_fullUrl"] for m in medications])
        entries.append(_entry(med_list))
        support_urls.append(med_list["_fullUrl"])

    # The referral order itself.
    service_request = _build_service_request(
        rich, direction, patient["_fullUrl"],
        ref_role["_fullUrl"] if ref_role else None,
        rnd_role["_fullUrl"] if rnd_role else None,
        [c["_fullUrl"] for c in conditions],
        [c.get("code") for c in conditions if c.get("code")],
        coverage["_fullUrl"] if coverage else None,
        support_urls)
    entries.append(_entry(service_request))

    task = _build_task(referral_id, patient["_fullUrl"], service_request["_fullUrl"])
    entries.append(_entry(task))

    prov = _build_provenance([service_request["_fullUrl"], task["_fullUrl"]])
    entries.append(_entry(prov))

    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


# --------------------------------------------------------------------------- #
def _build_patient(p: dict) -> dict:
    res = {"resourceType": "Patient", "_fullUrl": _urn(),
           "meta": {"profile": [
               "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"]},
           "identifier": [{"system": "http://hospital.example.org/mrn",
                           "value": p.get("mrn") or "MRN-UNKNOWN"}]}
    given = [g for g in [p.get("given")] if g]
    res["name"] = [{"family": p.get("family") or "", "given": given}]
    if p.get("gender"):
        res["gender"] = p["gender"]
    if p.get("birthDate"):
        res["birthDate"] = p["birthDate"]
    tel = []
    if p.get("phone"):
        tel.append({"system": "phone", "value": p["phone"], "use": "home"})
    if tel:
        res["telecom"] = tel
    addr = p.get("address") or {}
    if any(addr.get(k) for k in ("line", "city", "state", "postalCode")):
        a = {}
        if addr.get("line"):
            a["line"] = [addr["line"]]
        for k in ("city", "state", "postalCode"):
            if addr.get(k):
                a[k] = addr[k]
        res["address"] = [a]
    return res


def _build_provider_role(pr: dict) -> tuple[dict, dict | None, dict]:
    pract = {"resourceType": "Practitioner", "_fullUrl": _urn(),
             "meta": {"profile": [
                 "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner"]},
             "name": [{"text": pr.get("name") or "Unknown Provider"}]}
    if pr.get("npi"):
        pract["identifier"] = [{"system": config.SYS_NPI, "value": pr["npi"]}]

    org = None
    if pr.get("organization"):
        org = {"resourceType": "Organization", "_fullUrl": _urn(),
               "name": pr["organization"],
               "meta": {"profile": [
                   "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization"]}}

    role = {"resourceType": "PractitionerRole", "_fullUrl": _urn(),
            "practitioner": {"reference": pract["_fullUrl"]}}
    if org:
        role["organization"] = {"reference": org["_fullUrl"]}
    if pr.get("specialty"):
        role["specialty"] = [{"coding": [{"system": config.SYS_NUCC,
                                          "display": pr["specialty"]}],
                              "text": pr["specialty"]}]
    if pr.get("phone"):
        role["telecom"] = [{"system": "phone", "value": pr["phone"]}]
    return pract, org, role


def _build_payer_org(cov: dict) -> dict:
    return {"resourceType": "Organization", "_fullUrl": _urn(),
            "type": [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/organization-type",
                "code": "pay", "display": "Payer"}]}],
            "name": cov.get("payer") or "Unknown Payer"}


def _build_coverage(cov: dict, patient_url: str, payer_url: str) -> dict:
    res = {"resourceType": "Coverage", "_fullUrl": _urn(),
           "status": "active",
           "beneficiary": {"reference": patient_url},
           "payor": [{"reference": payer_url}]}
    if cov.get("member_id"):
        res["subscriberId"] = cov["member_id"]
        res["identifier"] = [{"system": "http://hospital.example.org/member-id",
                              "value": cov["member_id"]}]
    rel = cov.get("relationship")
    if rel:
        res["relationship"] = {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/subscriber-relationship",
            "code": rel}], "text": rel}
    if cov.get("group"):
        res["class"] = [{"type": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
            "code": "group"}]}, "value": cov["group"]}]
    if cov.get("plan"):
        res.setdefault("class", []).append({"type": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
            "code": "plan"}]}, "value": cov["plan"]})
    return res


def _dx_system(dx: dict) -> str:
    sys = (dx.get("system") or "icd10").lower()
    return {"icd10": config.SYS_ICD10, "icd-10": config.SYS_ICD10,
            "snomed": config.SYS_SNOMED, "cdt": config.SYS_CDT}.get(sys, config.SYS_ICD10)


def _build_condition(dx: dict, patient_url: str) -> dict | None:
    code = _codeable(dx.get("code"), _dx_system(dx), dx.get("display"))
    if not code:
        return None
    return {"resourceType": "Condition", "_fullUrl": _urn(),
            "meta": {"profile": [
                "http://hl7.org/fhir/us/core/StructureDefinition/"
                "us-core-condition-problems-health-concerns"]},
            "clinicalStatus": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "code": "active"}]},
            "category": [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-category",
                "code": "encounter-diagnosis"}]}],
            "code": code, "subject": {"reference": patient_url}}


def _build_medication(med: dict, patient_url: str) -> dict | None:
    """A current-medication-list entry -> MedicationRequest (RxNorm), mirroring the
    inbound ccda_to_fhir._build_medication so the two directions round-trip."""
    code = _codeable(med.get("code"), config.SYS_RXNORM, med.get("display"))
    if not code:
        return None
    return {"resourceType": "MedicationRequest", "_fullUrl": _urn(),
            "meta": {"profile": [
                "http://hl7.org/fhir/us/core/StructureDefinition/"
                "us-core-medicationrequest"]},
            "status": "active", "intent": "order",
            "medicationCodeableConcept": code,
            "subject": {"reference": patient_url}}


def _service_system(svc: dict) -> str:
    sys = (svc.get("system") or "cpt").lower()
    return {"cpt": config.SYS_CPT, "hcpcs": config.SYS_HCPCS,
            "loinc": config.SYS_LOINC, "cdt": config.SYS_CDT,
            "snomed": config.SYS_SNOMED}.get(sys, config.SYS_CPT)


def _drop_cdt_coding(code: dict) -> dict:
    """Strip CDT codings from a CodeableConcept (kept text) for medical-side receivers."""
    codings = [c for c in code.get("coding", []) if c.get("system") != config.SYS_CDT]
    out = {k: v for k, v in code.items() if k != "coding"}
    if codings:
        out["coding"] = codings
    return out


def _build_service_request(rich, direction, patient_url, requester_url, performer_url,
                           condition_urls, dx_codes, coverage_url,
                           support_urls=None) -> dict:
    svc = rich.get("service") or {}
    code = (_codeable(svc.get("code"), _service_system(svc), svc.get("display"))
            or {"coding": [{"system": config.SYS_SNOMED, "code": "306206005",
                            "display": "Referral to service"}], "text": "Referral"})
    # Must-support: where the receiving clinician bills medically (medical-to-dental,
    # dental-to-medical), CDT is not carried. Drop any CDT coding but keep the text so
    # the requested service is still legible.
    if direction in config.MEDICAL_SIDE_DIRECTIONS:
        code = _drop_cdt_coding(code)
    priority = (rich.get("priority") or "routine").lower()
    if priority not in PRIORITY_VALUES:
        priority = "routine"
    reason_codes = []
    if rich.get("reason_text"):
        reason_codes.append({"text": rich["reason_text"]})
    reason_codes.extend([c for c in dx_codes if c])

    profile = config.REFERRAL_PROFILE_BY_DIRECTION.get(
        direction, config.PROFILE_ODE_MED_TO_DENTAL)
    sr = {"resourceType": "ServiceRequest", "_fullUrl": _urn(),
          "meta": {"profile": [profile]},
          "identifier": [{"system": config.SYS_REFERRAL_ID,
                          "value": rich.get("referral_id")}],
          "status": "active", "intent": "order", "priority": priority,
          "code": code, "subject": {"reference": patient_url}}
    if reason_codes:
        sr["reasonCode"] = reason_codes
    if condition_urls:
        sr["reasonReference"] = [{"reference": u} for u in condition_urls]
    if requester_url:
        sr["requester"] = {"reference": requester_url}
    if performer_url:
        sr["performer"] = [{"reference": performer_url}]
    if coverage_url:
        sr["insurance"] = [{"reference": coverage_url}]
    if support_urls:
        sr["supportingInfo"] = [{"reference": u} for u in support_urls]
    if rich.get("supporting_info"):
        sr["note"] = [{"text": rich["supporting_info"]}]
    return sr


def _build_task(referral_id, patient_url, service_request_url) -> dict:
    return {"resourceType": "Task", "_fullUrl": _urn(),
            "meta": {"profile": [config.PROFILE_ODE_REFERRAL_TASK]},
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
            "status": "requested", "intent": "order",
            "businessStatus": cow.business_status_concept("received"),
            "code": {"coding": [{
                "system": "http://hl7.org/fhir/CodeSystem/task-code",
                "code": "fulfill"}]},
            "focus": {"reference": service_request_url},
            "for": {"reference": patient_url}}


def _build_provenance(target_urls: list[str]) -> dict:
    return {"resourceType": "Provenance", "_fullUrl": _urn(),
            "target": [{"reference": u} for u in target_urls],
            "recorded": "1970-01-01T00:00:00Z",  # stamped at runtime in engine
            "activity": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                "code": "CREATE"}]},
            "agent": [{"who": {"display": config.settings.adapter_id},
                       "type": {"coding": [{
                           "system": "http://terminology.hl7.org/CodeSystem/"
                           "provenance-participant-type",
                           "code": "assembler"}]}}]}
