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
from ode_adapter import hl7v2, registry, state_machine, ccda_to_fhir, fhir_to_ccda  # noqa: E402
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
