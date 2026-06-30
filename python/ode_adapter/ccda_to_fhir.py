"""Layer 3 (inbound) — C-CDA Referral Note  ->  FHIR transaction Bundle.

Parses a 360X Referral Request C-CDA (Referral Note) and decomposes it into the
ODE Native resources: Patient, Practitioner/Organization (referring), Condition,
MedicationRequest, AllergyIntolerance, a ServiceRequest (the referral), and a
Task (status=requested) plus a Provenance recording the translation.

Section -> resource mapping reuses the published C-CDA <-> US Core mappings
(see config.SECTION_LOINC). This reference creates one resource per coded entry it
finds; a production transformer should fully implement the C-CDA on FHIR mapping
and validate against US Core + ODE profiles.

Dental note: inbound medical content maps cleanly. Outbound dental content is the
lossy direction — handled in fhir_to_ccda.py.
"""
from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

from . import config

NS = {"v3": "urn:hl7-org:v3"}


def _q(tag: str) -> str:
    return f"{{urn:hl7-org:v3}}{tag}"


def _text(el) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def _urn() -> str:
    return f"urn:uuid:{uuid.uuid4()}"


def _coding(code_el, system_default: str = config.SYS_SNOMED) -> dict | None:
    if code_el is None:
        return None
    code = code_el.get("code")
    if not code:
        return None
    system = {
        "2.16.840.1.113883.6.96": config.SYS_SNOMED,
        "2.16.840.1.113883.6.1": config.SYS_LOINC,
        "2.16.840.1.113883.6.88": "http://www.nlm.nih.gov/research/umls/rxnorm",
    }.get(code_el.get("codeSystem"), system_default)
    coding = {"system": system, "code": code}
    if code_el.get("displayName"):
        coding["display"] = code_el.get("displayName")
    return {"coding": [coding], "text": code_el.get("displayName")}


def _entry(method: str, url: str, resource: dict) -> dict:
    return {
        "fullUrl": resource["_fullUrl"],
        "resource": {k: v for k, v in resource.items() if not k.startswith("_")},
        "request": {"method": method, "url": url},
    }


def transform_referral_note(cda_xml: str, *, referral_id: str) -> dict:
    """Return a FHIR transaction Bundle for a Referral Request C-CDA."""
    root = ET.fromstring(cda_xml)

    # --- Patient (recordTarget/patientRole) ---
    pr = root.find(".//v3:recordTarget/v3:patientRole", NS)
    patient = _build_patient(pr)

    # --- Referring author (author/assignedAuthor) ---
    author = root.find(".//v3:author/v3:assignedAuthor", NS)
    practitioner, organization = _build_author(author)

    entries: list[dict] = []
    supporting_refs: list[dict] = []
    reason_codes: list[dict] = []

    for res in (patient, practitioner, organization):
        if res:
            entries.append(_entry("POST", res["resourceType"], res))

    # --- Sections ---
    for section in root.findall(".//v3:component/v3:structuredBody/"
                                "v3:component/v3:section", NS):
        code_el = section.find("v3:code", NS)
        loinc = code_el.get("code") if code_el is not None else None
        kind = config.SECTION_LOINC.get(loinc)
        if not kind:
            continue
        narrative = _text(section.find("v3:text", NS))

        if kind == "problems":
            for obs in section.findall(".//v3:observation", NS):
                cond = _build_condition(obs, patient["_fullUrl"])
                if cond:
                    entries.append(_entry("POST", "Condition", cond))
                    supporting_refs.append({"reference": cond["_fullUrl"]})
        elif kind == "medications":
            for sa in section.findall(".//v3:substanceAdministration", NS):
                mr = _build_medication(sa, patient["_fullUrl"])
                if mr:
                    entries.append(_entry("POST", "MedicationRequest", mr))
        elif kind == "allergies":
            for obs in section.findall(".//v3:observation", NS):
                ai = _build_allergy(obs, patient["_fullUrl"])
                if ai:
                    entries.append(_entry("POST", "AllergyIntolerance", ai))
        elif kind == "reason_for_referral":
            rc = _coding(section.find(".//v3:observation/v3:value", NS))
            if rc:
                reason_codes.append(rc)
            else:
                reason_codes.append({"text": narrative[:500]})

    # --- ServiceRequest (the referral itself) ---
    service_request = _build_service_request(
        referral_id, patient["_fullUrl"],
        practitioner["_fullUrl"] if practitioner else None,
        reason_codes, supporting_refs,
    )
    entries.append(_entry("POST", "ServiceRequest", service_request))

    # --- Task (COW/ODE workflow object, status=requested) ---
    task = _build_task(referral_id, patient["_fullUrl"], service_request["_fullUrl"])
    entries.append(_entry("POST", "Task", task))

    # --- Provenance for the translation ---
    prov = _build_provenance([service_request["_fullUrl"], task["_fullUrl"]])
    entries.append(_entry("POST", "Provenance", prov))

    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


# --------------------------------------------------------------------------- #
# Resource builders
# --------------------------------------------------------------------------- #
def _build_patient(pr) -> dict:
    fu = _urn()
    res = {"resourceType": "Patient", "_fullUrl": fu,
           "meta": {"profile": [
               "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient"]}}
    if pr is None:
        return res
    idel = pr.find("v3:id", NS)
    if idel is not None and idel.get("extension"):
        res["identifier"] = [{"system": idel.get("root", "urn:oid:unknown"),
                              "value": idel.get("extension")}]
    name = pr.find("v3:patient/v3:name", NS)
    if name is not None:
        given = [_text(g) for g in name.findall("v3:given", NS)]
        family = _text(name.find("v3:family", NS))
        res["name"] = [{"family": family, "given": [g for g in given if g]}]
    gender = pr.find("v3:patient/v3:administrativeGenderCode", NS)
    if gender is not None and gender.get("code"):
        res["gender"] = {"M": "male", "F": "female"}.get(gender.get("code"), "unknown")
    bd = pr.find("v3:patient/v3:birthTime", NS)
    if bd is not None and bd.get("value"):
        v = bd.get("value")
        res["birthDate"] = f"{v[0:4]}-{v[4:6]}-{v[6:8]}"
    return res


def _build_author(author) -> tuple[dict | None, dict | None]:
    if author is None:
        return None, None
    pfu = _urn()
    practitioner = {"resourceType": "Practitioner", "_fullUrl": pfu,
                    "meta": {"profile": [
                        "http://hl7.org/fhir/us/core/StructureDefinition/"
                        "us-core-practitioner"]}}
    idel = author.find("v3:id", NS)
    if idel is not None and idel.get("extension"):
        practitioner["identifier"] = [{"system": config.SYS_NPI,
                                       "value": idel.get("extension")}]
    name = author.find("v3:assignedPerson/v3:name", NS)
    if name is not None:
        given = [_text(g) for g in name.findall("v3:given", NS)]
        practitioner["name"] = [{"family": _text(name.find("v3:family", NS)),
                                 "given": [g for g in given if g]}]
    org = None
    org_el = author.find("v3:representedOrganization", NS)
    if org_el is not None:
        org = {"resourceType": "Organization", "_fullUrl": _urn(),
               "name": _text(org_el.find("v3:name", NS)) or "Referring Organization",
               "meta": {"profile": [
                   "http://hl7.org/fhir/us/core/StructureDefinition/"
                   "us-core-organization"]}}
    return practitioner, org


def _build_condition(obs, patient_url: str) -> dict | None:
    code = _coding(obs.find("v3:value", NS))
    if not code:
        return None
    return {"resourceType": "Condition", "_fullUrl": _urn(),
            "clinicalStatus": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/condition-clinical",
                "code": "active"}]},
            "code": code, "subject": {"reference": patient_url}}


def _build_medication(sa, patient_url: str) -> dict | None:
    code = _coding(sa.find(".//v3:manufacturedMaterial/v3:code", NS),
                   system_default="http://www.nlm.nih.gov/research/umls/rxnorm")
    if not code:
        return None
    return {"resourceType": "MedicationRequest", "_fullUrl": _urn(),
            "status": "active", "intent": "order",
            "medicationCodeableConcept": code,
            "subject": {"reference": patient_url}}


def _build_allergy(obs, patient_url: str) -> dict | None:
    code = _coding(obs.find(".//v3:participant//v3:code", NS))
    if not code:
        code = _coding(obs.find("v3:value", NS))
    if not code:
        return None
    return {"resourceType": "AllergyIntolerance", "_fullUrl": _urn(),
            "clinicalStatus": {"coding": [{
                "system":
                "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                "code": "active"}]},
            "code": code, "patient": {"reference": patient_url}}


def _build_service_request(referral_id, patient_url, requester_url,
                           reason_codes, supporting_refs) -> dict:
    sr = {"resourceType": "ServiceRequest", "_fullUrl": _urn(),
          "identifier": [{"system": "urn:ohia:referral-id", "value": referral_id}],
          "status": "active", "intent": "order",
          "code": {"coding": [{"system": config.SYS_SNOMED, "code": "306206005",
                               "display": "Referral to service"}],
                   "text": "Dental referral"},
          "subject": {"reference": patient_url}}
    if requester_url:
        sr["requester"] = {"reference": requester_url}
    if reason_codes:
        sr["reasonCode"] = reason_codes
    if supporting_refs:
        sr["reasonReference"] = supporting_refs
    return sr


def _build_task(referral_id, patient_url, service_request_url) -> dict:
    return {"resourceType": "Task", "_fullUrl": _urn(),
            "identifier": [{"system": "urn:ohia:referral-id", "value": referral_id}],
            "status": "requested", "intent": "order",
            "code": {"coding": [{
                "system": "http://hl7.org/fhir/CodeSystem/task-code",
                "code": "fulfill"}]},
            "focus": {"reference": service_request_url},
            "for": {"reference": patient_url}}


def _build_provenance(target_urls: list[str]) -> dict:
    return {"resourceType": "Provenance", "_fullUrl": _urn(),
            "target": [{"reference": u} for u in target_urls],
            "recorded": "1970-01-01T00:00:00Z",  # set at runtime in engine
            "activity": {"coding": [{
                "system":
                "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                "code": "CREATE"}]},
            "agent": [{"who": {"display": config.settings.adapter_id},
                       "type": {"coding": [{
                           "system": "http://terminology.hl7.org/CodeSystem/"
                           "provenance-participant-type",
                           "code": "assembler"}]}}]}
