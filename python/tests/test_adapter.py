"""Adapter recheck — focused tests for the adapter core itself.

Independent of the stub. Covers inbound (PCC-55), cancellation (PCC-58), the
outbound state machine (PCC-56/57/58/59), the loss profile, content transforms,
correlation, plugin selection, and the closed loop. Stdlib-only:

    python3 tests/test_adapter.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ode_adapter.plugins  # noqa: E402,F401
from ode_adapter import (hl7v2, registry, state_machine, ccda_to_fhir,  # noqa: E402
                         fhir_to_ccda, cow, config, referral_fhir)
from ode_adapter.engine import Adapter  # noqa: E402

SAMPLE = os.path.join(ROOT, "samples", "referral_request.xml")


def _adapter():
    return Adapter(fhir=registry.create("fhir", "generic-r4", dry_run=True),
                   codec=registry.create("codec", "json-envelope"),
                   outbound=registry.create("transport", "capture"))


def _cda() -> str:
    with open(SAMPLE, encoding="utf-8") as fh:
        return fh.read()


def _pcc55(referral_id="REF-1001", with_cda=True) -> dict:
    return {
        "direct_message_id": "<m@x>", "submission_set_id": "sub-1",
        "sender_direct_address": "med@x", "recipient_direct_address": "dent@y",
        "transaction": "PCC-55",
        "hl7v2": hl7v2.build("OMG^O19", referral_id=referral_id),
        "documents": ([{"id": "doc-1", "mime_type": "text/xml", "content": _cda()}]
                      if with_cda else []),
    }


def _pcc58(referral_id) -> dict:
    return {
        "direct_message_id": "<m2@x>", "submission_set_id": "sub-1",
        "sender_direct_address": "med@x", "recipient_direct_address": "dent@y",
        "transaction": "PCC-58",
        "hl7v2": hl7v2.build("OSU^O51", referral_id=referral_id, order_status="CA"),
        "documents": [],
    }


def _types(bundle) -> set:
    return {e["resource"]["resourceType"] for e in bundle["entry"]}


def _find(bundle, rtype) -> dict | None:
    for e in bundle["entry"]:
        if e["resource"]["resourceType"] == rtype:
            return e["resource"]
    return None


DENTAL_RESULTS = [
    {"resourceType": "ClinicalImpression",
     "summary": "Active disease remediated; cleared for radiation."},
    {"resourceType": "Procedure",
     "code": {"coding": [{"system": "http://www.ada.org/cdt", "code": "D7140",
                          "display": "Extraction, erupted tooth"}],
              "text": "Extraction, erupted tooth"},
     "bodySite": [{"text": "30 (Universal)"}]},
    {"resourceType": "Observation", "_dental_perio": True,
     "code": {"text": "Perio pocket depth tooth 30"},
     "valueQuantity": {"value": 5, "unit": "mm"}},
    {"resourceType": "CarePlan", "description": "Recall in 3 months."},
]
MEDICAL_ONLY = [
    {"resourceType": "ClinicalImpression", "summary": "Cleared."},
    {"resourceType": "CarePlan", "description": "Routine follow-up."},
]
PATIENT = {"identifier": [{"system": "2.16.840.1.113883.19.5", "value": "MRN-1"}],
           "name": [{"given": ["Wilma"], "family": "Stonewright"}]}


def _task(status, referral="REF-1001"):
    return {"resourceType": "Task", "status": status,
            "identifier": [{"system": "urn:ohia:referral-id", "value": referral}]}


# ============================ INBOUND (PCC-55) ============================== #
def test_inbound_creates_expected_resources():
    res = _adapter().handle_inbound(_pcc55())
    b = res["bundle"]
    assert b["type"] == "transaction"
    assert all(e["request"]["method"] == "POST" for e in b["entry"])
    expected = {"Patient", "Practitioner", "Organization", "Condition",
                "MedicationRequest", "AllergyIntolerance", "ServiceRequest",
                "Task", "Provenance"}
    assert expected <= _types(b), expected - _types(b)


def test_inbound_task_is_requested_with_referral_id():
    res = _adapter().handle_inbound(_pcc55("REF-77"))
    assert res["referral_id"] == "REF-77"
    assert res["task_id"] and res["service_request_id"]
    task = _find(res["bundle"], "Task")
    sr = _find(res["bundle"], "ServiceRequest")
    assert task["status"] == "requested"
    for r in (task, sr):
        assert any(i["system"] == "urn:ohia:referral-id" and i["value"] == "REF-77"
                   for i in r["identifier"])


def test_inbound_condition_is_tongue_cancer():
    cond = _find(_adapter().handle_inbound(_pcc55())["bundle"], "Condition")
    label = (cond.get("code", {}).get("text") or "").lower()
    assert "tongue" in label, label


def test_provenance_is_stamped_at_runtime():
    prov = _find(_adapter().handle_inbound(_pcc55())["bundle"], "Provenance")
    assert prov["recorded"] != "1970-01-01T00:00:00Z"
    # parseable and recent-ish (this decade)
    yr = datetime.fromisoformat(prov["recorded"].replace("Z", "+00:00")).year
    assert yr >= 2025


def test_inbound_missing_cda_raises():
    try:
        _adapter().handle_inbound(_pcc55(with_cda=False))
    except ValueError as e:
        assert "C-CDA" in str(e) or "cda" in str(e).lower()
    else:
        raise AssertionError("expected ValueError for missing C-CDA")


def test_unsupported_inbound_transaction_raises():
    env = _pcc55()
    env["transaction"] = "PCC-99"
    try:
        _adapter().handle_inbound(env)
    except ValueError as e:
        assert "Unsupported" in str(e) or "PCC-99" in str(e)
    else:
        raise AssertionError("expected ValueError for unsupported transaction")


# ============================ CANCELLATION (PCC-58) ======================== #
def test_cancellation_revokes_task():
    a = _adapter()
    a.handle_inbound(_pcc55("REF-CX"))
    res = a.handle_inbound(_pcc58("REF-CX"))
    assert res["action"] == "task_cancelled" and res["task_id"]
    assert a.store.get("REF-CX").status == "cancelled"


def test_cancellation_without_episode_is_safe():
    res = _adapter().handle_inbound(_pcc58("REF-NONE"))
    assert res["action"] == "cancellation_no_episode"


# ============================ CORRELATION ================================== #
def test_episode_is_stored():
    a = _adapter()
    a.handle_inbound(_pcc55("REF-ST"))
    ep = a.store.get("REF-ST")
    assert ep is not None and ep.status == "requested" and ep.task_id
    assert len(a.store.all()) == 1


# ============================ OUTBOUND ===================================== #
def _emit(status, **kw):
    return _adapter().handle_task_event(_task(status), patient=PATIENT, **kw)


def test_outbound_completed_is_pcc57_with_document():
    out = _emit("completed", result_resources=MEDICAL_ONLY)
    assert out["transaction"] == "PCC-57"
    assert out["packaged"]["documents"], "PCC-57 should carry a C-CDA"
    assert hl7v2.parse(out["packaged"]["hl7v2"]).message_type == "OMG^O19"


def test_outbound_accept_is_pcc56_ip_no_document():
    out = _emit("accepted")
    assert out["transaction"] == "PCC-56"
    assert not out["packaged"]["documents"]
    assert hl7v2.parse(out["packaged"]["hl7v2"]).order_status == "IP"


def test_outbound_reject_is_pcc56_decline():
    out = _emit("rejected")
    assert out["transaction"] == "PCC-56"
    assert hl7v2.parse(out["packaged"]["hl7v2"]).order_status == "CA"


def test_outbound_interim_is_pcc59_with_document():
    out = _emit("in-progress", interim=True, result_resources=MEDICAL_ONLY)
    assert out["transaction"] == "PCC-59"
    assert out["packaged"]["documents"]


def test_outbound_cancelled_is_pcc58():
    assert _emit("cancelled")["transaction"] == "PCC-58"


def test_outbound_unknown_status_emits_nothing():
    out = _emit("draft")
    assert out["action"] == "no_outbound_for_status"


# ============================ LOSS PROFILE ================================= #
def test_loss_notes_for_dental_content():
    out = _emit("completed", result_resources=DENTAL_RESULTS)
    notes = " ".join(out["loss_notes"]).lower()
    assert len(out["loss_notes"]) == 2, out["loss_notes"]
    assert "cdt" in notes and "periodontal" in notes
    cda = out["packaged"]["documents"][0]["content"]
    assert "Dental Findings" in cda
    # the CDT *code* is not emitted as structured data — narrative only
    assert "D7140" not in cda


def test_no_loss_notes_for_medical_only():
    out = _emit("completed", result_resources=MEDICAL_ONLY)
    assert out["loss_notes"] == []
    assert "Dental Findings" not in out["packaged"]["documents"][0]["content"]


# ============================ STATE MACHINE (unit) ========================= #
def test_state_machine_mapping():
    cases = {
        "accepted":   ("PCC-56", "IP", False),
        "in-progress":("PCC-56", "IP", False),
        "rejected":   ("PCC-56", "CA", False),
        "failed":     ("PCC-56", "CA", False),
        "cancelled":  ("PCC-58", "CA", False),
        "completed":  ("PCC-57", "CM", True),
    }
    for status, (tx, os_, needs) in cases.items():
        d = state_machine.task_to_360x(status)
        assert d.transaction == tx and d.order_status == os_ and d.needs_document == needs, status
    assert state_machine.task_to_360x("draft") is None


def test_state_machine_interim_overrides_accept():
    d = state_machine.task_to_360x("in-progress", interim=True)
    assert d.transaction == "PCC-59" and d.needs_document


def test_appointment_event():
    assert state_machine.appointment_event(no_show=False).transaction == "PCC-60"
    assert state_machine.appointment_event(no_show=True).transaction == "PCC-61"


# ============================ TRANSFORMS (unit) ============================ #
def test_ccda_to_fhir_unit():
    b = ccda_to_fhir.transform_referral_note(_cda(), referral_id="REF-U")
    assert b["type"] == "transaction"
    assert {"Patient", "Condition", "ServiceRequest", "Task"} <= _types(b)


def test_fhir_to_ccda_dental_vs_medical_unit():
    cda_d, notes_d = fhir_to_ccda.build_consultation_note(
        patient=PATIENT, result_resources=DENTAL_RESULTS)
    cda_m, notes_m = fhir_to_ccda.build_consultation_note(
        patient=PATIENT, result_resources=MEDICAL_ONLY)
    assert notes_d and not notes_m
    assert "Assessment and Plan" in cda_d and "Assessment and Plan" in cda_m


# ============================ PLUGINS / FACTORY =========================== #
def test_registry_lists_expected_plugins():
    p = registry.all_plugins()
    assert "generic-r4" in p["fhir"] and "onyx" in p["fhir"]
    assert "json-envelope" in p["codec"] and "xdm-zip" in p["codec"]
    assert {"capture", "direct", "http"} <= set(p["transport"])


def test_from_config_builds_default_adapter():
    a = Adapter.from_config()
    assert a.fhir and a.codec and a.outbound


def test_onyx_backend_handles_inbound():
    a = Adapter(fhir=registry.create("fhir", "onyx", dry_run=True),
                codec=registry.create("codec", "json-envelope"),
                outbound=registry.create("transport", "capture"))
    res = a.handle_inbound(_pcc55("REF-ONYX"))
    assert res["referral_id"] == "REF-ONYX" and res["task_id"]


# ============================ CLOSED LOOP ================================= #
def test_inbound_then_outbound_loop():
    a = _adapter()
    inb = a.handle_inbound(_pcc55("REF-LOOP"))
    assert inb["referral_id"] == "REF-LOOP"
    out = a.handle_task_event(_task("completed", "REF-LOOP"),
                              patient=PATIENT, result_resources=DENTAL_RESULTS)
    assert out["transaction"] == "PCC-57" and out["referral_id"] == "REF-LOOP"
    assert a.outbound.sent and a.outbound.sent[0]["transaction"] == "PCC-57"


# ================= DENTAL-INITIATED: PCC-55 OUT (mirror) ================== #
def _dental_sr(referral="REF-D1"):
    return {"resourceType": "ServiceRequest", "id": "sr-" + referral,
            "identifier": [{"system": config.SYS_REFERRAL_ID, "value": referral}],
            "reasonCode": [{"text": "Dental clearance before radiation"}]}


def test_dental_initiation_emits_pcc55_referral_note():
    a = _adapter()
    out = a.handle_referral_initiation(
        referral_id="REF-D1", patient=PATIENT, service_request=_dental_sr(),
        conditions=[{"code": {"text": "Head and neck cancer"}}])
    assert out["transaction"] == "PCC-55"
    assert a.outbound.sent and a.outbound.sent[0]["transaction"] == "PCC-55"
    cda = out["packaged"]["documents"][0]["content"]
    assert config.DOC_REFERRAL_NOTE in cda           # 57133-1 Referral Note
    assert "Reason for Referral" in cda


def test_dental_initiation_opens_episode_referral_sent():
    a = _adapter()
    a.handle_referral_initiation(referral_id="REF-D2", patient=PATIENT,
                                 service_request=_dental_sr("REF-D2"))
    ep = a.store.get("REF-D2")
    assert ep is not None and ep.initiated_by == "dental"
    assert ep.business_status == "referral-sent"


def test_dental_referral_note_flags_dental_loss():
    _cda, notes = fhir_to_ccda.build_referral_note(
        patient=PATIENT, conditions=[{"code": {"coding": [
            {"system": config.SYS_CDT, "code": "D0150"}], "text": "Comprehensive eval"}}])
    assert notes and "narrative only" in " ".join(notes).lower()


# ================= REPLY INGESTION: 360X -> COW/FHIR ====================== #
def _reply_env(tx, referral, *, order_status=None, cda=None, appt=None, **v2kw):
    mtype = hl7v2.TX_MESSAGE_TYPE[tx]
    docs = [{"id": "d", "mime_type": "text/xml", "content": cda}] if cda else []
    return {"direct_message_id": "<r@x>", "submission_set_id": "s",
            "sender_direct_address": "med@x", "recipient_direct_address": "dent@y",
            "transaction": tx,
            "hl7v2": hl7v2.build(mtype, referral, order_status=order_status,
                                 appointment_start=appt, **v2kw),
            "documents": docs}


def _consult_cda():
    cda, _ = fhir_to_ccda.build_consultation_note(
        patient=PATIENT, result_resources=MEDICAL_ONLY)
    return cda


def _initiated():
    a = _adapter()
    a.handle_referral_initiation(referral_id="REF-D1", patient=PATIENT,
                                 service_request=_dental_sr())
    return a


def test_reply_pcc56_accept_sets_task_accepted():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-56", "REF-D1", order_status="IP"))
    assert r["task_status"] == "accepted" and r["business_status"] == "accepted"
    assert a.store.get("REF-D1").status == "accepted"


def test_reply_pcc56_decline_revokes_request():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-56", "REF-D1", order_status="CA"))
    assert r["task_status"] == "rejected" and r["business_status"] == "declined"
    assert r["request_status"] == "revoked"


def test_reply_pcc56_accept_carries_owner_note_and_period():
    """Accept is not a bare status flip: it carries the accepting provider
    (Task.owner), an acknowledgment (Task.note), and an expected timeframe
    (Task.restriction.period.end), degraded from ORC-12/NTE/ORC-15."""
    a = _initiated()
    r = a.handle_inbound(_reply_env(
        "PCC-56", "REF-D1", order_status="IP",
        accepting_provider="1841299305^Dr. Nadia Patel",
        note="Accepted; patient will be seen.", period_end="20260715"))
    task = r["task"]
    assert task["owner"]["reference"]  # PractitionerRole owner recorded
    assert task["note"][0]["text"] == "Accepted; patient will be seen."
    assert task["restriction"]["period"]["end"] == "2026-07-15"
    # The accepting provider identity is written for the inbox.
    assert "PractitionerRole" in {x["resourceType"] for x in r["resources"]}


def test_reply_pcc56_decline_carries_coded_status_reason():
    a = _initiated()
    r = a.handle_inbound(_reply_env(
        "PCC-56", "REF-D1", order_status="CA", status_reason="capacity",
        note="No capacity this quarter."))
    sr = r["task"]["statusReason"]
    assert sr["coding"][0]["system"] == config.DECLINE_REASON_SYSTEM
    assert sr["coding"][0]["code"] == "capacity"
    assert r["task"]["note"][0]["text"] == "No capacity this quarter."


def test_reply_pcc57_outcome_completes_and_writes_resources():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-57", "REF-D1", cda=_consult_cda()))
    assert r["task_status"] == "completed" and r["business_status"] == "outcome-final"
    assert r["request_status"] == "completed"
    types = {x["resourceType"] for x in r["resources"]}
    assert {"ClinicalImpression", "DocumentReference"} <= types


def test_reply_pcc57_missing_cda_raises():
    a = _initiated()
    try:
        a.handle_inbound(_reply_env("PCC-57", "REF-D1"))
    except ValueError as e:
        assert "Consultation Note" in str(e) or "C-CDA" in str(e)
    else:
        raise AssertionError("expected ValueError for missing consultation note")


def test_reply_pcc59_interim_in_progress():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-59", "REF-D1", cda=_consult_cda()))
    assert r["task_status"] == "in-progress" and r["business_status"] == "interim-results"


def test_reply_pcc60_appointment_writes_booked_appointment():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-60", "REF-D1", appt="20260715090000"))
    assert r["business_status"] == "appointment-booked"
    appt = r["resources"][0]
    assert appt["resourceType"] == "Appointment" and appt["status"] == "booked"
    assert appt["start"].startswith("2026-07-15")


def test_reply_pcc61_noshow_writes_communication():
    a = _initiated()
    r = a.handle_inbound(_reply_env("PCC-61", "REF-D1"))
    assert r["business_status"] == "appointment-noshow"
    assert r["resources"][0]["resourceType"] == "Communication"


def test_reply_without_prior_initiation_is_safe():
    a = _adapter()
    r = a.handle_inbound(_reply_env("PCC-56", "REF-ORPHAN", order_status="IP"))
    assert r["task_status"] == "accepted"
    assert a.store.get("REF-ORPHAN") is not None


def test_reply_ingestion_caches_inbox():
    a = _initiated()
    a.handle_inbound(_reply_env("PCC-57", "REF-D1", cda=_consult_cda()))
    ep = a.store.get("REF-D1")
    assert ep.inbox, "reply resources should be cached for the harness inbox"
    assert any(r["resourceType"] == "DocumentReference" for r in ep.inbox)


# ================= UNIT: reply mapping + consult parser =================== #
def test_reply_to_fhir_mapping():
    assert state_machine.reply_to_fhir("PCC-56", "IP").task_status == "accepted"
    assert state_machine.reply_to_fhir("PCC-56", "CA").task_status == "rejected"
    assert state_machine.reply_to_fhir("PCC-57").business_status == "outcome-final"
    assert state_machine.reply_to_fhir("PCC-59").task_status == "in-progress"
    assert state_machine.reply_to_fhir("PCC-60").business_status == "appointment-booked"
    assert state_machine.reply_to_fhir("PCC-61").business_status == "appointment-noshow"
    assert state_machine.reply_to_fhir("PCC-55") is None


def test_transform_consultation_note_unit():
    b = ccda_to_fhir.transform_consultation_note(_consult_cda(), referral_id="REF-U")
    assert b["type"] == "transaction"
    assert {"Patient", "ClinicalImpression", "DocumentReference"} <= _types(b)


def test_business_status_concept_uses_dental_value_set():
    c = cow.business_status_concept("outcome-final")
    assert c["coding"][0]["system"] == config.COW_BUSINESS_STATUS_SYSTEM


def test_dental_profiles_applied_to_results():
    res = [dict(r) for r in DENTAL_RESULTS]
    cow.apply_dental_profiles(res)
    proc = next(r for r in res if r["resourceType"] == "Procedure")
    assert config.PROFILE_DENTAL_PROCEDURE in proc["meta"]["profile"]


# ================= OUTBOUND: appointment (SIU) mirror ==================== #
def test_outbound_appointment_event_emits_pcc60():
    a = _adapter()
    out = a.handle_appointment_event(referral_id="REF-A", appointment_start="20260715")
    assert out["transaction"] == "PCC-60"
    assert hl7v2.parse(out["packaged"]["hl7v2"]).message_type == "SIU^S12"


def test_outbound_appointment_noshow_emits_pcc61():
    a = _adapter()
    out = a.handle_appointment_event(referral_id="REF-A", no_show=True)
    assert out["transaction"] == "PCC-61"


# ================= SCENARIOS: full loops both directions ================= #
def test_scenario_dental_initiated_end_to_end():
    """Dental PMS initiates; medical peer accepts then sends the outcome."""
    a = _adapter()
    a.handle_referral_initiation(referral_id="REF-SC1", patient=PATIENT,
                                 service_request=_dental_sr("REF-SC1"))
    a.handle_inbound(_reply_env("PCC-56", "REF-SC1", order_status="IP"))
    a.handle_inbound(_reply_env("PCC-57", "REF-SC1", cda=_consult_cda()))
    ep = a.store.get("REF-SC1")
    assert ep.status == "completed" and ep.business_status == "outcome-final"
    assert a.outbound.sent[0]["transaction"] == "PCC-55"


def test_scenario_medical_initiated_end_to_end():
    """Medical EHR initiates (inbound PCC-55); dental fulfiller completes (outbound)."""
    a = _adapter()
    inb = a.handle_inbound(_pcc55("REF-SC2"))
    assert inb["task_id"]
    out = a.handle_task_event(_task("completed", "REF-SC2"),
                              patient=PATIENT, result_resources=DENTAL_RESULTS)
    assert out["transaction"] == "PCC-57" and out["business_status"] == "outcome-final"


# ============ CONFORMANCE: ODE Native contract (openapi.yaml) ============ #
def _rich_med_to_dental(referral_id="REF-CONF"):
    """A medical->dental intake whose service is (wrongly) CDT-coded, to prove the
    bridge drops CDT for a medical-side receiver."""
    return {
        "referral_id": referral_id, "direction": "medical-to-dental",
        "patient": {"given": "John", "family": "Smith", "mrn": "MRN-9"},
        "diagnoses": [{"system": "icd10", "code": "C02.1",
                       "display": "Malignant neoplasm of border of tongue"}],
        "service": {"system": "cdt", "code": "D0150",
                    "display": "Comprehensive oral evaluation"},
        "medications": [{"code": "860975", "display": "metformin 500 MG"},
                        {"code": "314076", "display": "lisinopril 10 MG"}],
    }


def _entries(bundle, rtype):
    """(fullUrl, resource) pairs for a resource type — resources have been stripped of
    the internal `_fullUrl`, so the entry's fullUrl is the reference target."""
    return [(e["fullUrl"], e["resource"]) for e in bundle["entry"]
            if e["resource"]["resourceType"] == rtype]


def _find_all(bundle, rtype):
    return [r for _, r in _entries(bundle, rtype)]


def test_conformance_medical_to_dental_profile_and_codes():
    b = referral_fhir.build_referral_bundle(_rich_med_to_dental())
    sr = _find_all(b, "ServiceRequest")[0]
    assert sr["meta"]["profile"] == [config.PROFILE_ODE_MED_TO_DENTAL]
    # No CDT on a medical-side receiver; ICD-10 reasonCode present.
    systems = {c["system"] for c in sr["code"].get("coding", [])}
    assert config.SYS_CDT not in systems
    reason_systems = {c["system"] for rc in sr.get("reasonCode", [])
                      for c in rc.get("coding", [])}
    assert config.SYS_ICD10 in reason_systems


def test_conformance_medication_list_present_and_linked():
    b = referral_fhir.build_referral_bundle(_rich_med_to_dental())
    lists = _entries(b, "List")
    assert len(lists) == 1
    list_url, med_list = lists[0]
    assert med_list["meta"]["profile"] == [config.PROFILE_ODE_MEDICATION_LIST]
    assert med_list["status"] == "current" and med_list["mode"] == "snapshot"
    assert med_list["code"]["coding"][0]["code"] == config.MED_LIST_LOINC
    mr_urls = {u for u, _ in _entries(b, "MedicationRequest")}
    med_refs = {e["item"]["reference"] for e in med_list["entry"]}
    assert med_refs == mr_urls and len(med_refs) == 2
    # The ServiceRequest references the List (not each MedicationRequest).
    sr = _find_all(b, "ServiceRequest")[0]
    support = {s["reference"] for s in sr.get("supportingInfo", [])}
    assert list_url in support


def test_conformance_referral_task_profile_and_business_status():
    b = referral_fhir.build_referral_bundle(_rich_med_to_dental())
    task = _find_all(b, "Task")[0]
    assert task["meta"]["profile"] == [config.PROFILE_ODE_REFERRAL_TASK]
    bs = task["businessStatus"]["coding"][0]
    assert bs["system"] == "http://ohia-codes.org/CodeSystem/ode-referral-sub-status"
    assert bs["code"] == "received"


def test_conformance_dental_to_dental_keeps_cdt():
    rich = _rich_med_to_dental("REF-D2D")
    rich["direction"] = "dental-to-dental"
    rich["service"] = {"system": "cdt", "code": "D7210", "display": "Surgical extraction"}
    b = referral_fhir.build_referral_bundle(rich)
    sr = _find_all(b, "ServiceRequest")[0]
    assert sr["meta"]["profile"] == [config.PROFILE_ODE_DENTAL_TO_DENTAL]
    systems = {c["system"] for c in sr["code"].get("coding", [])}
    assert config.SYS_CDT in systems


# ============================ runner ===================================== #
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed, {len(tests)} total")
    sys.exit(1 if failed else 0)
