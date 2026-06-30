"""Tests for the stub 360X sender (the stub acting as Referral Initiator).

Stdlib-only; run with `python3 tests/test_stub_sender.py`. The important tests build
the inbound messages and run them through the REAL adapter, proving the stub's
sending produces something the adapter accepts — and exercise the full two-way loop:

    stub sends PCC-55 -> adapter creates Task -> dental completes ->
    adapter emits PCC-57 -> stub receives it.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # python/ dir

import ode_adapter.plugins  # noqa: E402,F401
from ode_adapter import hl7v2, registry  # noqa: E402
from ode_adapter.engine import Adapter  # noqa: E402
from tools import stub_360x_sender as sender  # noqa: E402
from tools.stub_360x_core import Stub360XReceiver  # noqa: E402


def _adapter():
    return Adapter(fhir=registry.create("fhir", "generic-r4", dry_run=True),
                   codec=registry.create("codec", "json-envelope"),
                   outbound=registry.create("transport", "capture"))


# ---- envelope shape -------------------------------------------------------- #
def test_referral_request_shape():
    env = sender.build_referral_request("REF-2001")
    assert env["transaction"] == "PCC-55"
    assert env["documents"] and env["documents"][0]["mime_type"] == "text/xml"
    assert env["documents"][0]["content"].strip(), "CDA content should be non-empty"
    assert env["sender_direct_address"] and env["recipient_direct_address"]
    assert env["direct_message_id"] and env["submission_set_id"]


def test_referral_v2_carries_referral_id():
    """The v2 message must carry the referral id so the adapter extracts it."""
    env = sender.build_referral_request("REF-2002")
    parsed = hl7v2.parse(env["hl7v2"])
    assert parsed.message_type == "OMG^O19"
    assert parsed.referral_id == "REF-2002"


def test_cancellation_shape_and_v2():
    env = sender.build_cancellation("REF-2003")
    assert env["transaction"] == "PCC-58"
    parsed = hl7v2.parse(env["hl7v2"])
    assert parsed.message_type == "OSU^O51"
    assert parsed.referral_id == "REF-2003"
    assert parsed.order_status == "CA"


# ---- the adapter actually accepts what the stub sends ---------------------- #
def test_adapter_accepts_sent_referral():
    env = sender.build_referral_request("REF-3001")
    result = _adapter().handle_inbound(env)
    assert result["referral_id"] == "REF-3001"
    assert result["task_id"], "adapter should create a Task"
    assert result["service_request_id"], "adapter should create a ServiceRequest"
    # the referral note's clinical content made it into the FHIR bundle
    types = {e["resource"]["resourceType"] for e in result["bundle"]["entry"]}
    assert "ServiceRequest" in types and "Task" in types


def test_custom_referral_id_flows_through():
    for rid in ("REF-A", "REF-B", "REF-C"):
        env = sender.build_referral_request(rid)
        assert _adapter().handle_inbound(env)["referral_id"] == rid


def test_adapter_accepts_sent_cancellation():
    a = _adapter()
    a.handle_inbound(sender.build_referral_request("REF-4001"))      # open it
    res = a.handle_inbound(sender.build_cancellation("REF-4001"))    # cancel it
    assert res["action"] == "task_cancelled"
    assert res["task_id"]


def test_cancellation_without_prior_referral_is_safe():
    res = _adapter().handle_inbound(sender.build_cancellation("REF-UNKNOWN"))
    assert res["action"] == "cancellation_no_episode"


# ---- full two-way loop (the point of a peer) ------------------------------- #
def test_full_round_trip_stub_to_adapter_to_stub():
    adapter = _adapter()
    receiver = Stub360XReceiver()

    # 1) stub SENDS a referral; adapter accepts and opens a Task
    sent = adapter.handle_inbound(sender.build_referral_request("REF-5001"))
    assert sent["referral_id"] == "REF-5001"

    # 2) dental side completes; adapter emits an outbound PCC-57
    task = {"resourceType": "Task", "status": "completed",
            "identifier": [{"system": "urn:ohia:referral-id", "value": "REF-5001"}]}
    outcome = adapter.handle_task_event(
        task, patient={"name": [{"given": ["John"], "family": "Smith"}]},
        result_resources=[{"resourceType": "ClinicalImpression",
                           "summary": "cleared for radiation"}])
    assert outcome["transaction"] == "PCC-57"

    # 3) stub RECEIVES the outcome
    ack = receiver.receive(outcome["packaged"])
    assert ack["ack"] == "received" and ack["transaction"] == "PCC-57"
    assert len(receiver.received("PCC-57")) == 1


def test_custom_cda_content_is_sent():
    env = sender.build_referral_request("REF-6001", cda_content="<ClinicalDocument/>")
    assert env["documents"][0]["content"] == "<ClinicalDocument/>"


# ---- runner ---------------------------------------------------------------- #
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
