"""Tests for the stub 360X receiver core.

Stdlib-only so they run with `python3 tests/test_stub_360x.py` (no pytest/FastAPI
needed). Also import-compatible with pytest. The key tests drive the receiver with
the REAL envelope the adapter emits, for each outbound transaction the adapter can
produce, proving the stub fully supports receiving.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # the python/ dir

import ode_adapter.plugins  # noqa: E402,F401
from ode_adapter import registry  # noqa: E402
from ode_adapter.engine import Adapter  # noqa: E402
from tools.stub_360x_core import (  # noqa: E402
    Stub360XReceiver, EnvelopeError, normalize_envelope, KNOWN_TRANSACTIONS)


# ---- helpers --------------------------------------------------------------- #
def _adapter():
    return Adapter(fhir=registry.create("fhir", "generic-r4", dry_run=True),
                   codec=registry.create("codec", "json-envelope"),
                   outbound=registry.create("transport", "capture"))

def _task(status, referral="REF-1001"):
    return {"resourceType": "Task", "status": status,
            "identifier": [{"system": "urn:ohia:referral-id", "value": referral}]}

PATIENT = {"name": [{"given": ["John"], "family": "Smith"}]}
RESULTS = [{"resourceType": "ClinicalImpression", "summary": "cleared for radiation"}]

def _outbound(status, **kw):
    """Produce the real packaged envelope the adapter would send for a Task status."""
    a = _adapter()
    out = a.handle_task_event(_task(status), patient=PATIENT, **kw)
    return out["packaged"]


# ---- tests ----------------------------------------------------------------- #
def test_receives_real_adapter_outcome():
    """The headline case: a real PCC-57 outcome from the adapter is received & stored."""
    pkg = _outbound("completed", result_resources=RESULTS)
    assert pkg["transaction"] == "PCC-57"
    r = Stub360XReceiver()
    ack = r.receive(pkg)
    assert ack["ack"] == "received"
    assert ack["transaction"] == "PCC-57"
    assert "received_at" in ack
    stored = r.received()
    assert len(stored) == 1
    assert stored[0]["transaction"] == "PCC-57"
    assert stored[0]["transaction_name"] == KNOWN_TRANSACTIONS["PCC-57"]


def test_receives_every_outbound_transaction_the_adapter_emits():
    """Drive the adapter through the statuses that yield outbound messages and
    confirm the stub accepts each one."""
    cases = []
    # statuses that produce outbound 360X (depends on the state machine)
    for status, kw in [
        ("accepted", {}),
        ("in-progress", {"interim": True, "result_resources": RESULTS}),
        ("completed", {"result_resources": RESULTS}),
    ]:
        out = _adapter().handle_task_event(_task(status), patient=PATIENT, **kw)
        if out.get("packaged"):           # some statuses may not emit; skip those
            cases.append(out["packaged"])
    assert cases, "expected at least one outbound message from the adapter"
    r = Stub360XReceiver()
    for pkg in cases:
        ack = r.receive(pkg)
        assert ack["ack"] == "received"
        assert ack["transaction"] in KNOWN_TRANSACTIONS
    assert len(r.received()) == len(cases)


def test_document_content_is_preserved():
    """C-CDA payload must survive receipt intact (id/mime_type/content)."""
    pkg = _outbound("completed", result_resources=RESULTS)
    assert pkg["documents"], "expected a C-CDA document on PCC-57"
    r = Stub360XReceiver()
    r.receive(pkg)
    got = r.received()[0]["envelope"]["documents"][0]
    assert set(got) >= {"id", "mime_type", "content"}
    assert got["content"] == pkg["documents"][0]["content"]
    assert "xml" in got["mime_type"]


def test_synthetic_transactions_accepted():
    """PCC-58/60/61 (which the adapter may emit in other flows) are accepted."""
    r = Stub360XReceiver()
    for tx in ("PCC-55", "PCC-56", "PCC-58", "PCC-59", "PCC-60", "PCC-61"):
        ack = r.receive({"transaction": tx})
        assert ack["transaction"] == tx
    assert len(r.received()) == 6


def test_unknown_transaction_rejected():
    r = Stub360XReceiver()
    try:
        r.receive({"transaction": "PCC-99"})
    except EnvelopeError as e:
        assert "unknown" in str(e).lower()
    else:
        raise AssertionError("expected EnvelopeError for unknown transaction")
    assert r.received() == []  # nothing stored on rejection


def test_missing_transaction_rejected():
    r = Stub360XReceiver()
    for bad in [{}, {"sender_direct_address": "x"}, "not-a-dict", None]:
        try:
            r.receive(bad)
        except EnvelopeError:
            pass
        else:
            raise AssertionError(f"expected EnvelopeError for {bad!r}")


def test_missing_optional_fields_default():
    """Only transaction is required; the rest default like the adapter's shape."""
    env = normalize_envelope({"transaction": "PCC-57"})
    assert env["sender_direct_address"] == ""
    assert env["recipient_direct_address"] == ""
    assert env["hl7v2"] == ""
    assert env["documents"] == []


def test_malformed_documents_rejected():
    r = Stub360XReceiver()
    for bad_docs in ["x", [123], [{"id": "a"}, "nope"]]:
        try:
            r.receive({"transaction": "PCC-57", "documents": bad_docs})
        except EnvelopeError:
            pass
        else:
            raise AssertionError(f"expected EnvelopeError for documents={bad_docs!r}")


def test_filter_clear_and_health():
    r = Stub360XReceiver()
    r.receive({"transaction": "PCC-57"})
    r.receive({"transaction": "PCC-56"})
    r.receive({"transaction": "PCC-57"})
    assert len(r.received()) == 3
    assert len(r.received("PCC-57")) == 2
    assert len(r.received("PCC-56")) == 1
    assert r.health() == {"status": "ok", "received_count": 3}
    assert r.clear() == 3
    assert r.received() == []
    assert r.health()["received_count"] == 0


def test_messages_accumulate_in_order():
    r = Stub360XReceiver()
    order = ["PCC-56", "PCC-59", "PCC-57"]
    for tx in order:
        r.receive({"transaction": tx})
    assert [x["transaction"] for x in r.received()] == order


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
