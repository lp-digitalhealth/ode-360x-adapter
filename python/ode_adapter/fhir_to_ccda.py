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
    lis = "".join(f"<item>{_esc(i)}</item>" for i in items)
    return f"<list>{lis}</list>"
