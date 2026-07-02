"""Layer 3 (outbound) — FHIR result  ->  C-CDA Consultation Note.

Builds the 360X Referral Outcome (PCC-57) or Interim Consultation Note (PCC-59)
C-CDA from the completed/updated ODE Task and its result resources
(ClinicalImpression, Procedure, CarePlan, Observation).

THE LOSS PROFILE LIVES HERE. Dental-origin structured data has no slot in C-CDA:
  - CDT procedures (http://www.ada.org/cdt)
  - tooth numbering (Universal / FDI ISO 3950)
  - periodontal observations
...so this transformer renders them into the human-readable narrative block and
flags them, rather than dropping them. Receiving medical systems get the text; the
structure is preserved only on the ODE Native (FHIR) path. This asymmetry is the
architectural argument for ODE Native end-to-end.
"""
from __future__ import annotations

import html
from datetime import datetime, timezone

from . import config

CDA_HEADER_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <realmCode code="US"/>
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/>
  <templateId root="2.16.840.1.113883.10.20.22.1.4"/>
  <code code="{doc_code}" codeSystem="2.16.840.1.113883.6.1"
        displayName="{doc_name}"/>
  <title>{title}</title>
  <effectiveTime value="{ts}"/>
  <confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>
  <recordTarget><patientRole>{patient}</patientRole></recordTarget>
  <author><time value="{ts}"/><assignedAuthor>
    <id root="urn:ohia" extension="{adapter}"/>
    <assignedAuthoringDevice><softwareName>{adapter}</softwareName></assignedAuthoringDevice>
  </assignedAuthor></author>
  <component><structuredBody>
{sections}
  </structuredBody></component>
</ClinicalDocument>
"""

SECTION_TEMPLATE = """    <component><section>
      <code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{name}"/>
      <title>{title}</title>
      <text>{text}</text>
    </section></component>"""


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _patient_fragment(patient: dict | None) -> str:
    if not patient:
        return '<id nullFlavor="UNK"/>'
    ident = (patient.get("identifier") or [{}])[0]
    name = (patient.get("name") or [{}])[0]
    given = " ".join(name.get("given", []))
    return (
        f'<id root="{_esc(ident.get("system","urn:ohia"))}" '
        f'extension="{_esc(ident.get("value",""))}"/>'
        f'<patient><name><given>{_esc(given)}</given>'
        f'<family>{_esc(name.get("family",""))}</family></name></patient>'
    )


def _is_dental(coding: dict) -> bool:
    return any(c.get("system") == config.SYS_CDT
              for c in coding.get("code", {}).get("coding", []))


def build_consultation_note(*, patient: dict | None, result_resources: list[dict],
                            interim: bool = False) -> tuple[str, list[str]]:
    """Return (cda_xml, loss_notes).

    `loss_notes` lists dental-origin items that could only be carried as narrative.
    """
    sections: list[str] = []
    loss_notes: list[str] = []

    # Assessment & plan narrative assembled from result resources.
    lines: list[str] = []
    dental_lines: list[str] = []
    for res in result_resources:
        rtype = res.get("resourceType")
        code = res.get("code", {})
        display = code.get("text") or _first_display(code)
        if rtype == "Procedure":
            if _is_dental({"code": code}):
                tooth = _tooth_text(res)
                dental_lines.append(f"Dental procedure (CDT): {display}{tooth}")
                loss_notes.append(f"CDT procedure '{display}' -> narrative only")
            else:
                lines.append(f"Procedure: {display}")
        elif rtype == "Observation" and _is_perio(res):
            dental_lines.append(f"Periodontal finding: {display} = {_obs_value(res)}")
            loss_notes.append(f"Periodontal observation '{display}' -> narrative only")
        elif rtype == "ClinicalImpression":
            lines.append("Assessment: " + (res.get("summary") or display or ""))
        elif rtype == "CarePlan":
            lines.append("Plan: " + (res.get("description") or display or ""))

    assess_text = _list_to_xhtml(lines or ["Referral completed."])
    sections.append(SECTION_TEMPLATE.format(
        code="51847-2", name="Assessment and Plan", title="Assessment and Plan",
        text=assess_text))

    if dental_lines:
        # Dental content that has no structured C-CDA home -> explicit narrative section.
        dental_text = _list_to_xhtml(
            ["NOTE: The following dental-specific findings are conveyed as narrative; "
             "structured representation is available via ODE Native (FHIR)."]
            + dental_lines)
        sections.append(SECTION_TEMPLATE.format(
            code="34109-9", name="Note", title="Dental Findings (narrative)",
            text=dental_text))

    doc_name = "Consultation Note"
    title = "Interim Consultation Note" if interim else "Referral Outcome — Consultation Note"
    cda = CDA_HEADER_TEMPLATE.format(
        doc_code=config.DOC_CONSULTATION_NOTE, doc_name=doc_name, title=_esc(title),
        ts=_ts(), patient=_patient_fragment(patient),
        adapter=_esc(config.settings.adapter_id),
        sections="\n".join(sections))
    return cda, loss_notes


def build_referral_note(*, patient: dict | None = None,
                        service_request: dict | None = None,
                        conditions: list[dict] | None = None,
                        reason_text: str | None = None,
                        rich: dict | None = None) -> tuple[str, list[str]]:
    """Build a 360X Referral Request C-CDA (Referral Note, LOINC 57133-1).

    When `rich` (the referral intake) is supplied, the note carries a
    referral-grade dataset — referring + rendering providers in the header, the
    coverage/payer, coded diagnoses (ICD-10), the requested service (Plan of
    Treatment), priority, and clinical justification — i.e. enough for the receiver
    to diagnose, treat, and bill. Otherwise the legacy FHIR-resource signature is
    used (patient/service_request/conditions/reason_text).

    Returns (cda_xml, loss_notes); `loss_notes` flags any dental-origin content that
    could only be carried as narrative (mirror of build_consultation_note).
    """
    if rich is not None:
        return _build_rich_referral_note(rich)
    sections: list[str] = []
    loss_notes: list[str] = []
    conditions = conditions or []

    # --- Reason for referral ---
    reason = reason_text
    if not reason and service_request:
        rc = service_request.get("reasonCode") or []
        if rc:
            reason = rc[0].get("text") or _first_display(rc[0])
    sections.append(SECTION_TEMPLATE.format(
        code="42349-1", name="Reason for Referral", title="Reason for Referral",
        text=_list_to_xhtml([reason or "Referral for evaluation."])))

    # --- Problems ---
    problem_lines: list[str] = []
    dental_lines: list[str] = []
    for cond in conditions:
        code = cond.get("code", {})
        display = code.get("text") or _first_display(code)
        if _is_dental({"code": code}):
            dental_lines.append(f"Dental condition: {display}")
            loss_notes.append(f"Dental condition '{display}' -> narrative only")
        else:
            problem_lines.append(f"Problem: {display}")
    if problem_lines:
        sections.append(SECTION_TEMPLATE.format(
            code="11450-4", name="Problem List", title="Problems",
            text=_list_to_xhtml(problem_lines)))
    if dental_lines:
        sections.append(SECTION_TEMPLATE.format(
            code="34109-9", name="Note", title="Dental Findings (narrative)",
            text=_list_to_xhtml(
                ["NOTE: dental-specific findings conveyed as narrative; structured "
                 "representation is available via ODE Native (FHIR)."] + dental_lines)))

    cda = CDA_HEADER_TEMPLATE.format(
        doc_code=config.DOC_REFERRAL_NOTE, doc_name="Referral Note",
        title=_esc("Referral Request"), ts=_ts(),
        patient=_patient_fragment(patient),
        adapter=_esc(config.settings.adapter_id),
        sections="\n".join(sections))
    return cda, loss_notes


# --------------------------------------------------------------------------- #
# Rich Referral Note (referral-grade: diagnose + treat + bill + providers)
# --------------------------------------------------------------------------- #
REFERRAL_HEADER_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<ClinicalDocument xmlns="urn:hl7-org:v3">
  <realmCode code="US"/>
  <typeId root="2.16.840.1.113883.1.3" extension="POCD_HD000040"/>
  <templateId root="2.16.840.1.113883.10.20.22.1.4"/>
  <code code="{doc_code}" codeSystem="2.16.840.1.113883.6.1" displayName="Referral Note"/>
  <title>Referral Request</title>
  <effectiveTime value="{ts}"/>
  <confidentialityCode code="N" codeSystem="2.16.840.1.113883.5.25"/>
  <recordTarget><patientRole>{patient}</patientRole></recordTarget>
  <author><time value="{ts}"/><assignedAuthor>{referring}</assignedAuthor></author>
  <informationRecipient><intendedRecipient>{rendering}</intendedRecipient></informationRecipient>
  <component><structuredBody>
{sections}
  </structuredBody></component>
</ClinicalDocument>
"""

# OID for each diagnosis / service code system (mirror of config.OID_TO_SYSTEM).
_SYSTEM_OID = {
    config.SYS_ICD10: "2.16.840.1.113883.6.90",
    config.SYS_SNOMED: "2.16.840.1.113883.6.96",
    config.SYS_CPT: "2.16.840.1.113883.6.12",
    config.SYS_HCPCS: "2.16.840.1.113883.6.285",
    config.SYS_LOINC: "2.16.840.1.113883.6.1",
    config.SYS_RXNORM: "2.16.840.1.113883.6.88",
    config.SYS_CDT: "2.16.840.1.113883.6.13",
}
_DX_SYS = {"icd10": config.SYS_ICD10, "icd-10": config.SYS_ICD10,
           "snomed": config.SYS_SNOMED, "cdt": config.SYS_CDT}
_SVC_SYS = {"cpt": config.SYS_CPT, "hcpcs": config.SYS_HCPCS,
            "loinc": config.SYS_LOINC, "cdt": config.SYS_CDT,
            "snomed": config.SYS_SNOMED}
_MED_SYS = {"rxnorm": config.SYS_RXNORM, "snomed": config.SYS_SNOMED}


def _build_rich_referral_note(rich: dict) -> tuple[str, list[str]]:
    loss_notes: list[str] = []
    sections: list[str] = []

    priority = (rich.get("priority") or "routine")
    reason = rich.get("reason_text") or "Referral for evaluation."
    reason_items = [f"Priority: {priority}", reason]
    sections.append(SECTION_TEMPLATE.format(
        code="42349-1", name="Reason for Referral", title="Reason for Referral",
        text=_list_to_xhtml(reason_items)))

    # --- Problems / diagnoses (coded; ICD-10 for diagnose + bill) ---
    problem_entries: list[str] = []
    problem_lines: list[str] = []
    dental_lines: list[str] = []
    for dx in (rich.get("diagnoses") or []):
        code = dx.get("code")
        display = dx.get("display") or code or ""
        sys_key = (dx.get("system") or "icd10").lower()
        if sys_key == "cdt":
            dental_lines.append(f"Dental diagnosis (CDT): {display}")
            loss_notes.append(f"Dental diagnosis '{display}' -> narrative only")
            continue
        fhir_sys = _DX_SYS.get(sys_key, config.SYS_ICD10)
        oid = _SYSTEM_OID.get(fhir_sys, "2.16.840.1.113883.6.90")
        if code:
            problem_entries.append(_coded_observation_entry(code, oid, display))
        problem_lines.append(f"{display} ({code})" if code else display)
    if problem_lines or problem_entries:
        sections.append(_section_with_entries(
            "11450-4", "Problem List", "Problems / Diagnoses",
            problem_lines or ["See coded entries."], problem_entries))

    # --- Current medications (supporting clinical context) ---
    med_entries: list[str] = []
    med_lines: list[str] = []
    for med in (rich.get("medications") or []):
        code = med.get("code")
        display = med.get("display") or code or ""
        sys_key = (med.get("system") or "rxnorm").lower()
        fhir_sys = _MED_SYS.get(sys_key, config.SYS_RXNORM)
        oid = _SYSTEM_OID.get(fhir_sys, "2.16.840.1.113883.6.88")
        if code:
            med_entries.append(_medication_entry(code, oid, display))
        med_lines.append(f"{display} ({code})" if code else display)
    if med_lines or med_entries:
        sections.append(_section_with_entries(
            "10160-0", "Medications", "Current Medications",
            med_lines or ["See coded entries."], med_entries))

    # --- Plan of treatment: the requested service (treat + bill) ---
    svc = rich.get("service") or {}
    if svc.get("code") or svc.get("display"):
        sys_key = (svc.get("system") or "cpt").lower()
        fhir_sys = _SVC_SYS.get(sys_key, config.SYS_CPT)
        oid = _SYSTEM_OID.get(fhir_sys, "2.16.840.1.113883.6.12")
        entry = _requested_service_entry(svc.get("code"), oid, svc.get("display"))
        sections.append(_section_with_entries(
            "18776-5", "Plan of Treatment", "Requested Service",
            [f"Requested: {svc.get('display') or svc.get('code')}"
             + (f" ({svc.get('code')})" if svc.get("code") else "")], [entry]))

    # --- Payers / coverage (bill) ---
    cov = rich.get("coverage") or {}
    cov_lines = []
    if cov.get("payer"):
        cov_lines.append(f"Payer: {cov['payer']}")
    if cov.get("member_id"):
        cov_lines.append(f"Member ID: {cov['member_id']}")
    if cov.get("group"):
        cov_lines.append(f"Group: {cov['group']}")
    if cov.get("plan"):
        cov_lines.append(f"Plan: {cov['plan']}")
    if cov.get("relationship"):
        cov_lines.append(f"Relationship: {cov['relationship']}")
    if cov_lines:
        sections.append(SECTION_TEMPLATE.format(
            code="48768-6", name="Payers", title="Insurance / Coverage",
            text=_list_to_xhtml(cov_lines)))

    # --- Clinical information / justification (diagnose + treat) ---
    if rich.get("supporting_info"):
        sections.append(SECTION_TEMPLATE.format(
            code="10164-2", name="History of Present Illness",
            title="Clinical Information", text=_list_to_xhtml([rich["supporting_info"]])))

    if dental_lines:
        sections.append(SECTION_TEMPLATE.format(
            code="34109-9", name="Note", title="Dental Findings (narrative)",
            text=_list_to_xhtml(
                ["NOTE: dental-specific findings conveyed as narrative; structured "
                 "representation is available via ODE Native (FHIR)."] + dental_lines)))

    cda = REFERRAL_HEADER_TEMPLATE.format(
        doc_code=config.DOC_REFERRAL_NOTE, ts=_ts(),
        patient=_rich_patient_fragment(rich.get("patient") or {}),
        referring=_provider_fragment(rich.get("referring_provider") or {}, author=True),
        rendering=_provider_fragment(rich.get("rendering_provider") or {}, author=False),
        sections="\n".join(sections))
    return cda, loss_notes


def _rich_patient_fragment(p: dict) -> str:
    mrn_sys = "http://hospital.example.org/mrn"
    frags = [f'<id root="{_esc(mrn_sys)}" extension="{_esc(p.get("mrn",""))}"/>']
    addr = p.get("address") or {}
    if any(addr.get(k) for k in ("line", "city", "state", "postalCode")):
        frags.append(
            "<addr>"
            + (f"<streetAddressLine>{_esc(addr.get('line',''))}</streetAddressLine>"
               if addr.get("line") else "")
            + (f"<city>{_esc(addr.get('city',''))}</city>" if addr.get("city") else "")
            + (f"<state>{_esc(addr.get('state',''))}</state>" if addr.get("state") else "")
            + (f"<postalCode>{_esc(addr.get('postalCode',''))}</postalCode>"
               if addr.get("postalCode") else "")
            + "</addr>")
    if p.get("phone"):
        frags.append(f'<telecom value="tel:{_esc(p["phone"])}"/>')
    gender = {"male": "M", "female": "F"}.get((p.get("gender") or "").lower(), "UN")
    bd = (p.get("birthDate") or "").replace("-", "")
    frags.append(
        f'<patient><name><given>{_esc(p.get("given",""))}</given>'
        f'<family>{_esc(p.get("family",""))}</family></name>'
        f'<administrativeGenderCode code="{gender}"/>'
        + (f'<birthTime value="{_esc(bd)}"/>' if bd else "")
        + "</patient>")
    return "".join(frags)


def _provider_fragment(pr: dict, *, author: bool) -> str:
    """assignedAuthor (referring) or intendedRecipient (rendering) body."""
    frags = []
    if pr.get("npi"):
        frags.append(f'<id root="{config.SYS_NPI}" extension="{_esc(pr["npi"])}"/>')
    else:
        frags.append('<id nullFlavor="UNK"/>')
    if pr.get("specialty"):
        frags.append(f'<code codeSystem="2.16.840.1.113883.6.101" '
                     f'displayName="{_esc(pr["specialty"])}"/>')
    name = f'<name>{_esc(pr.get("name","Unknown Provider"))}</name>'
    if author:
        frags.append(f"<assignedPerson>{name}</assignedPerson>")
        if pr.get("organization"):
            frags.append(f'<representedOrganization><name>'
                         f'{_esc(pr["organization"])}</name></representedOrganization>')
    else:
        frags.append(f"<informationRecipient>{name}</informationRecipient>")
        if pr.get("organization"):
            frags.append(f'<receivedOrganization><name>'
                         f'{_esc(pr["organization"])}</name></receivedOrganization>')
    return "".join(frags)


def _coded_observation_entry(code: str, oid: str, display: str) -> str:
    return (f'<entry><observation classCode="OBS" moodCode="EVN">'
            f'<value code="{_esc(code)}" codeSystem="{oid}" '
            f'displayName="{_esc(display)}"/></observation></entry>')


def _requested_service_entry(code: str | None, oid: str, display: str | None) -> str:
    code_attr = f'code="{_esc(code)}" codeSystem="{oid}" ' if code else ""
    return (f'<entry><procedure classCode="PROC" moodCode="RQO">'
            f'<code {code_attr}displayName="{_esc(display or "")}"/>'
            f'</procedure></entry>')


def _medication_entry(code: str | None, oid: str, display: str | None) -> str:
    code_attr = f'code="{_esc(code)}" codeSystem="{oid}" ' if code else ""
    return ('<entry><substanceAdministration classCode="SBADM" moodCode="EVN">'
            '<consumable><manufacturedProduct><manufacturedMaterial>'
            f'<code {code_attr}displayName="{_esc(display or "")}"/>'
            '</manufacturedMaterial></manufacturedProduct></consumable>'
            '</substanceAdministration></entry>')


def _section_with_entries(code: str, name: str, title: str,
                          lines: list[str], entries: list[str]) -> str:
    return ("    <component><section>\n"
            f'      <code code="{code}" codeSystem="2.16.840.1.113883.6.1" '
            f'displayName="{_esc(name)}"/>\n'
            f"      <title>{_esc(title)}</title>\n"
            f"      <text>{_list_to_xhtml(lines)}</text>\n"
            f"      {''.join(entries)}\n"
            "    </section></component>")


# --------------------------------------------------------------------------- #
def _first_display(code: dict) -> str:
    for c in code.get("coding", []):
        if c.get("display"):
            return c["display"]
    return ""


def _is_perio(res: dict) -> bool:
    prof = (res.get("meta") or {}).get("profile", [])
    return any("perio" in p.lower() for p in prof) or res.get("_dental_perio") is True


def _tooth_text(res: dict) -> str:
    bs = res.get("bodySite") or []
    for b in bs:
        if b.get("text"):
            return f" (tooth {b['text']})"
    return ""


def _obs_value(res: dict) -> str:
    vq = res.get("valueQuantity")
    if vq:
        return f"{vq.get('value')} {vq.get('unit','')}".strip()
    return res.get("valueString", "")


def _list_to_xhtml(items: list[str]) -> str:
    # Trailing newline becomes each <item>'s tail text, so a parser reading
    # itertext() sees one line per item (items aren't run together).
    lis = "".join(f"<item>{_esc(i)}</item>\n" for i in items)
    return f"<list>{lis}</list>"
