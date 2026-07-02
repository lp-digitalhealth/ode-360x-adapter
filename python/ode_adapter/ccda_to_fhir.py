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
degraded direction — handled in fhir_to_ccda.py.
"""
from __future__ import annotations

import base64
import uuid
import xml.etree.ElementTree as ET

from . import config, cow

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
    system = config.OID_TO_SYSTEM.get(code_el.get("codeSystem"), system_default)
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


def transform_referral_note(cda_xml: str, *, referral_id: str,
                            direction: str | None = None) -> dict:
    """Return a FHIR transaction Bundle for a Referral Request C-CDA.

    Reconstructs the referral-grade dataset the receiver needs to diagnose, treat,
    and bill: Patient (+ address/phone), the referring provider (author) and
    rendering provider (informationRecipient) as Practitioner/PractitionerRole/
    Organization, coded diagnoses (Condition), Coverage (payers section), and a
    ServiceRequest that ties them together (requested service, priority, requester,
    performer, reasonCode/reasonReference, insurance, supporting note) + a Task.
    """
    root = ET.fromstring(cda_xml)
    direction = (direction or config.DEFAULT_DIRECTION).lower()

    # --- Patient (recordTarget/patientRole) ---
    pr = root.find(".//v3:recordTarget/v3:patientRole", NS)
    patient = _build_patient(pr)
    patient_url = patient["_fullUrl"]

    entries: list[dict] = [_entry("POST", "Patient", patient)]

    # --- Referring provider (author) and rendering provider (recipient) ---
    ref_role_url = _add_provider(
        entries, root.find(".//v3:author/v3:assignedAuthor", NS), author=True)
    rnd_role_url = _add_provider(
        entries, root.find(".//v3:informationRecipient/v3:intendedRecipient", NS),
        author=False)

    supporting_refs: list[dict] = []
    support_info_refs: list[dict] = []
    med_urls: list[str] = []
    reason_codes: list[dict] = []
    service_code: dict | None = None
    priority = "routine"
    supporting_note: str | None = None
    coverage_url: str | None = None

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
                cond = _build_condition(obs, patient_url)
                if cond:
                    entries.append(_entry("POST", "Condition", cond))
                    supporting_refs.append({"reference": cond["_fullUrl"]})
        elif kind == "medications":
            for sa in section.findall(".//v3:substanceAdministration", NS):
                mr = _build_medication(sa, patient_url)
                if mr:
                    entries.append(_entry("POST", "MedicationRequest", mr))
                    med_urls.append(mr["_fullUrl"])
        elif kind == "allergies":
            for obs in section.findall(".//v3:observation", NS):
                ai = _build_allergy(obs, patient_url)
                if ai:
                    entries.append(_entry("POST", "AllergyIntolerance", ai))
        elif kind == "reason_for_referral":
            reason_codes.append({"text": _strip_priority(narrative)[:500]})
            priority = _parse_priority(narrative) or priority
        elif kind == "plan_of_treatment":
            service_code = _coding(section.find(".//v3:procedure/v3:code", NS),
                                   system_default=config.SYS_CPT) or service_code
        elif kind == "payers":
            cov, payer_org = _build_coverage(narrative, patient_url)
            if cov:
                if payer_org:
                    entries.append(_entry("POST", "Organization", payer_org))
                entries.append(_entry("POST", "Coverage", cov))
                coverage_url = cov["_fullUrl"]
        elif kind == "clinical_info":
            supporting_note = narrative or None

    # --- ODEMedicationList (List) aggregating the current medications ---
    if med_urls:
        med_list = cow.build_medication_list(referral_id, patient_url, med_urls)
        entries.append(_entry("POST", "List", med_list))
        support_info_refs.append({"reference": med_list["_fullUrl"]})

    # --- ServiceRequest (the referral itself) ---
    service_request = _build_service_request(
        referral_id, direction, patient_url, ref_role_url, rnd_role_url, reason_codes,
        supporting_refs, service_code=service_code, priority=priority,
        coverage_url=coverage_url, note=supporting_note,
        support_info_refs=support_info_refs)
    entries.append(_entry("POST", "ServiceRequest", service_request))

    # --- Task (COW/ODE workflow object, status=requested) ---
    task = _build_task(referral_id, patient_url, service_request["_fullUrl"])
    entries.append(_entry("POST", "Task", task))

    # --- Provenance for the translation ---
    prov = _build_provenance([service_request["_fullUrl"], task["_fullUrl"]])
    entries.append(_entry("POST", "Provenance", prov))

    return {"resourceType": "Bundle", "type": "transaction", "entry": entries}


def _add_provider(entries: list[dict], el, *, author: bool) -> str | None:
    """Parse an assignedAuthor/intendedRecipient into Practitioner (+ Organization)
    + PractitionerRole; append them and return the PractitionerRole fullUrl."""
    practitioner, org, role = _build_provider_role(el, author=author)
    for res in (practitioner, org, role):
        if res:
            entries.append(_entry("POST", res["resourceType"], res))
    return role["_fullUrl"] if role else None


def transform_consultation_note(cda_xml: str, *, referral_id: str,
                                interim: bool = False) -> dict:
    """Parse a 360X Consultation Note C-CDA (PCC-57 outcome or PCC-59 interim) into a
    FHIR transaction Bundle of outcome resources.

    This is the mirror of fhir_to_ccda.build_consultation_note: the receiving side
    of the loop. It decomposes the note into Patient, a ClinicalImpression
    (assessment), any structured Procedure/Observation entries, a CarePlan (plan),
    and a DocumentReference wrapping the whole note so the original document is
    retained. Dental narrative carried only as text (the loss profile) is preserved
    in the DocumentReference.
    """
    root = ET.fromstring(cda_xml)

    pr = root.find(".//v3:recordTarget/v3:patientRole", NS)
    patient = _build_patient(pr)
    patient_url = patient["_fullUrl"]

    entries: list[dict] = [_entry("POST", "Patient", patient)]
    outcome_urls: list[str] = []

    for section in root.findall(".//v3:component/v3:structuredBody/"
                                "v3:component/v3:section", NS):
        code_el = section.find("v3:code", NS)
        loinc = code_el.get("code") if code_el is not None else None
        narrative = _text(section.find("v3:text", NS))

        if loinc == "51847-2":          # Assessment and Plan
            ci = _build_clinical_impression(narrative, patient_url)
            entries.append(_entry("POST", "ClinicalImpression", ci))
            outcome_urls.append(ci["_fullUrl"])
            cp = _build_care_plan(narrative, patient_url)
            if cp:
                entries.append(_entry("POST", "CarePlan", cp))
                outcome_urls.append(cp["_fullUrl"])
        elif loinc == "47519-4":        # Procedures (structured, if present)
            for proc in section.findall(".//v3:procedure", NS):
                p = _build_procedure(proc, patient_url)
                if p:
                    entries.append(_entry("POST", "Procedure", p))
                    outcome_urls.append(p["_fullUrl"])
        elif loinc == "30954-2":        # Results (structured, if present)
            for obs in section.findall(".//v3:observation", NS):
                o = _build_result_observation(obs, patient_url)
                if o:
                    entries.append(_entry("POST", "Observation", o))
                    outcome_urls.append(o["_fullUrl"])

    docref = _build_document_reference(cda_xml, patient_url, referral_id,
                                       interim=interim)
    entries.append(_entry("POST", "DocumentReference", docref))
    outcome_urls.append(docref["_fullUrl"])

    prov = _build_provenance(outcome_urls)
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
    tel = pr.find("v3:telecom", NS)
    if tel is not None and tel.get("value"):
        res["telecom"] = [{"system": "phone",
                           "value": tel.get("value").replace("tel:", "")}]
    addr = pr.find("v3:addr", NS)
    if addr is not None:
        a = {}
        line = _text(addr.find("v3:streetAddressLine", NS))
        if line:
            a["line"] = [line]
        for tag, key in (("city", "city"), ("state", "state"),
                         ("postalCode", "postalCode")):
            val = _text(addr.find(f"v3:{tag}", NS))
            if val:
                a[key] = val
        if a:
            res["address"] = [a]
    return res


def _build_provider_role(el, *, author: bool) -> tuple[dict | None, dict | None,
                                                       dict | None]:
    """Parse assignedAuthor (referring) or intendedRecipient (rendering) into
    Practitioner (+ Organization) + PractitionerRole. Returns (pract, org, role)."""
    if el is None:
        return None, None, None
    practitioner = {"resourceType": "Practitioner", "_fullUrl": _urn(),
                    "meta": {"profile": [
                        "http://hl7.org/fhir/us/core/StructureDefinition/"
                        "us-core-practitioner"]}}
    idel = el.find("v3:id", NS)
    if idel is not None and idel.get("extension"):
        practitioner["identifier"] = [{"system": config.SYS_NPI,
                                       "value": idel.get("extension")}]
    name_el = (el.find("v3:assignedPerson/v3:name", NS) if author
               else el.find("v3:informationRecipient/v3:name", NS))
    if name_el is not None:
        text = _text(name_el)
        family = _text(name_el.find("v3:family", NS))
        given = [_text(g) for g in name_el.findall("v3:given", NS)]
        if family or given:
            practitioner["name"] = [{"family": family,
                                     "given": [g for g in given if g]}]
        elif text:
            practitioner["name"] = [{"text": text}]

    org = None
    org_el = (el.find("v3:representedOrganization", NS) if author
              else el.find("v3:receivedOrganization", NS))
    if org_el is not None and _text(org_el.find("v3:name", NS)):
        org = {"resourceType": "Organization", "_fullUrl": _urn(),
               "name": _text(org_el.find("v3:name", NS)),
               "meta": {"profile": [
                   "http://hl7.org/fhir/us/core/StructureDefinition/"
                   "us-core-organization"]}}

    role = {"resourceType": "PractitionerRole", "_fullUrl": _urn(),
            "practitioner": {"reference": practitioner["_fullUrl"]}}
    if org:
        role["organization"] = {"reference": org["_fullUrl"]}
    spec = el.find("v3:code", NS)
    if spec is not None and spec.get("displayName"):
        role["specialty"] = [{"coding": [{"system": config.SYS_NUCC,
                                          "display": spec.get("displayName")}],
                              "text": spec.get("displayName")}]
    return practitioner, org, role


def _build_coverage(narrative: str, patient_url: str) -> tuple[dict | None,
                                                               dict | None]:
    """Parse the Payers section narrative ('Payer: X', 'Member ID: Y', ...) into a
    Coverage (+ payer Organization)."""
    fields: dict[str, str] = {}
    for line in narrative.replace(";", "\n").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
    payer = fields.get("payer")
    member = fields.get("member id") or fields.get("member")
    if not payer and not member:
        return None, None
    payer_org = None
    payor = []
    if payer:
        payer_org = {"resourceType": "Organization", "_fullUrl": _urn(),
                     "type": [{"coding": [{
                         "system": "http://terminology.hl7.org/CodeSystem/"
                         "organization-type", "code": "pay", "display": "Payer"}]}],
                     "name": payer}
        payor = [{"reference": payer_org["_fullUrl"]}]
    cov = {"resourceType": "Coverage", "_fullUrl": _urn(),
           "status": "active", "beneficiary": {"reference": patient_url},
           "payor": payor or [{"display": "Unknown Payer"}]}
    if member:
        cov["subscriberId"] = member
        cov["identifier"] = [{"system": "http://hospital.example.org/member-id",
                              "value": member}]
    if fields.get("relationship"):
        cov["relationship"] = {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/subscriber-relationship",
            "code": fields["relationship"]}], "text": fields["relationship"]}
    klass = []
    if fields.get("group"):
        klass.append({"type": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
            "code": "group"}]}, "value": fields["group"]})
    if fields.get("plan"):
        klass.append({"type": {"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/coverage-class",
            "code": "plan"}]}, "value": fields["plan"]})
    if klass:
        cov["class"] = klass
    return cov, payer_org


_PRIORITY_WORDS = {"routine", "urgent", "asap", "stat"}


def _parse_priority(narrative: str) -> str | None:
    low = (narrative or "").lower()
    if "priority:" in low:
        after = low.split("priority:", 1)[1].strip()
        for w in _PRIORITY_WORDS:
            if after.startswith(w):
                return w
    return None


def _strip_priority(narrative: str) -> str:
    """Drop a leading 'Priority: X' item from the reason narrative."""
    parts = [p.strip() for p in (narrative or "").splitlines() if p.strip()]
    parts = [p for p in parts if not p.lower().startswith("priority:")]
    return " ".join(parts).strip() or (narrative or "").strip()


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


def _build_service_request(referral_id, direction, patient_url, requester_url,
                           performer_url, reason_codes, supporting_refs, *,
                           service_code=None, priority="routine",
                           coverage_url=None, note=None,
                           support_info_refs=None) -> dict:
    code = service_code or {"coding": [{"system": config.SYS_SNOMED,
                                        "code": "306206005",
                                        "display": "Referral to service"}],
                            "text": "Referral"}
    # Medical-side receivers don't carry CDT (see referral_fhir); keep text.
    if direction in config.MEDICAL_SIDE_DIRECTIONS:
        codings = [c for c in code.get("coding", [])
                   if c.get("system") != config.SYS_CDT]
        code = {**{k: v for k, v in code.items() if k != "coding"},
                **({"coding": codings} if codings else {})}
    profile = config.REFERRAL_PROFILE_BY_DIRECTION.get(
        direction, config.PROFILE_ODE_MED_TO_DENTAL)
    sr = {"resourceType": "ServiceRequest", "_fullUrl": _urn(),
          "meta": {"profile": [profile]},
          "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
          "status": "active", "intent": "order",
          "priority": priority if priority in ("routine", "urgent", "asap", "stat")
          else "routine",
          "code": code, "subject": {"reference": patient_url}}
    if requester_url:
        sr["requester"] = {"reference": requester_url}
    if performer_url:
        sr["performer"] = [{"reference": performer_url}]
    if reason_codes:
        sr["reasonCode"] = reason_codes
    if supporting_refs:
        sr["reasonReference"] = supporting_refs
    if support_info_refs:
        sr["supportingInfo"] = support_info_refs
    if coverage_url:
        sr["insurance"] = [{"reference": coverage_url}]
    if note:
        sr["note"] = [{"text": note}]
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


# --------------------------------------------------------------------------- #
# Consultation-note (reply) builders
# --------------------------------------------------------------------------- #
def _build_clinical_impression(narrative: str, patient_url: str) -> dict:
    summary = _assessment_text(narrative) or narrative or "Referral outcome."
    return {"resourceType": "ClinicalImpression", "_fullUrl": _urn(),
            "status": "completed", "subject": {"reference": patient_url},
            "summary": summary[:1000]}


def _build_care_plan(narrative: str, patient_url: str) -> dict | None:
    plan = _plan_text(narrative)
    if not plan:
        return None
    return {"resourceType": "CarePlan", "_fullUrl": _urn(),
            "status": "active", "intent": "plan",
            "subject": {"reference": patient_url}, "description": plan[:1000]}


def _build_procedure(proc, patient_url: str) -> dict | None:
    code = _coding(proc.find("v3:code", NS))
    if not code:
        return None
    return {"resourceType": "Procedure", "_fullUrl": _urn(),
            "status": "completed", "code": code,
            "subject": {"reference": patient_url}}


def _build_result_observation(obs, patient_url: str) -> dict | None:
    code = _coding(obs.find("v3:code", NS))
    if not code:
        return None
    res = {"resourceType": "Observation", "_fullUrl": _urn(),
           "status": "final", "code": code, "subject": {"reference": patient_url}}
    val = obs.find("v3:value", NS)
    if val is not None:
        if val.get("value"):
            res["valueQuantity"] = {"value": _num(val.get("value")),
                                    "unit": val.get("unit", "")}
        elif _text(val):
            res["valueString"] = _text(val)
    return res


def _build_document_reference(cda_xml: str, patient_url: str, referral_id: str,
                              *, interim: bool) -> dict:
    code = config.DOC_CONSULTATION_NOTE
    data = base64.b64encode(cda_xml.encode("utf-8")).decode("ascii")
    return {"resourceType": "DocumentReference", "_fullUrl": _urn(),
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral_id}],
            "status": "current",
            "type": {"coding": [{"system": config.SYS_LOINC, "code": code,
                                 "display": "Consultation Note"}]},
            "subject": {"reference": patient_url},
            "description": ("Interim consultation note" if interim
                            else "Referral outcome consultation note"),
            "content": [{"attachment": {"contentType": "application/xml",
                                        "data": data}}]}


def _assessment_text(narrative: str) -> str:
    # Our outbound note prefixes assessment lines with "Assessment:"; keep the gist.
    for line in narrative.replace(".", ".\n").splitlines():
        if "assessment" in line.lower():
            return line.strip()
    return narrative.strip()


def _plan_text(narrative: str) -> str:
    for line in narrative.replace(".", ".\n").splitlines():
        if line.lower().strip().startswith("plan"):
            return line.split(":", 1)[-1].strip()
    return ""


def _num(value: str):
    try:
        return float(value) if "." in value else int(value)
    except (TypeError, ValueError):
        return value
