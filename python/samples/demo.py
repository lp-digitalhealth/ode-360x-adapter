"""End-to-end demo of the 360/ODE Adapter (runs with no live FHIR server).

    python -m samples.demo            # from the repo root, with deps installed
    # or:  python samples/demo.py

It:
  1. Builds an inbound 360X Referral Request (PCC-55) envelope from the sample
     C-CDA, runs it through the adapter, and prints the resulting FHIR Bundle.
  2. Simulates the dental side completing the referral with dental-specific
     results (a CDT procedure + a periodontal observation), runs the outbound
     path, and prints the 360X Referral Outcome (PCC-57) C-CDA plus the LOSS
     NOTES showing which dental data could only be carried as narrative.
  3. Writes samples/inbound_pcc55.json so you can exercise the HTTP API with curl.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ode_adapter import hl7v2, registry              # noqa: E402
from ode_adapter.engine import Adapter               # noqa: E402
import ode_adapter.plugins                            # noqa: E402,F401  (registers built-ins)

HERE = os.path.dirname(os.path.abspath(__file__))
REFERRAL_ID = "REF-1001"


def build_inbound_envelope() -> dict:
    with open(os.path.join(HERE, "referral_request.xml"), encoding="utf-8") as fh:
        cda = fh.read()
    return {
        "direct_message_id": "<msg-7788@oncology.example.org>",
        "submission_set_id": "1.2.840.114350.1.13.99.1.7788",
        "sender_direct_address": "referrals@oncology.direct.example.org",
        "recipient_direct_address": "intake@dentalgroup.direct.example.org",
        "transaction": "PCC-55",
        "hl7v2": hl7v2.build("OMG^O19", referral_id=REFERRAL_ID),
        "documents": [
            {"id": "doc-1", "mime_type": "text/xml", "content": cda},
        ],
    }


def simulated_completion() -> dict:
    """What the adapter would receive from ODE Native when the dentist finishes."""
    task = {
        "resourceType": "Task", "id": "task-1", "status": "completed",
        "identifier": [{"system": "urn:ohia:referral-id", "value": REFERRAL_ID}],
    }
    patient = {
        "identifier": [{"system": "2.16.840.1.113883.19.5", "value": "MRN-558211"}],
        "name": [{"given": ["Wilma"], "family": "Stonewright"}],
    }
    result_resources = [
        {"resourceType": "ClinicalImpression",
         "summary": "Active dental disease remediated; patient cleared for radiation."},
        {"resourceType": "Procedure",
         "code": {"coding": [{"system": "http://www.ada.org/cdt", "code": "D7140",
                              "display": "Extraction, erupted tooth"}],
                  "text": "Extraction, erupted tooth"},
         "bodySite": [{"text": "30 (Universal)"}]},
        {"resourceType": "Observation", "_dental_perio": True,
         "code": {"text": "Periodontal pocket depth, tooth 30 distal"},
         "valueQuantity": {"value": 5, "unit": "mm"}},
        {"resourceType": "CarePlan",
         "description": "Routine recall in 3 months; maintain oral hygiene during therapy."},
    ]
    return {"task": task, "patient": patient, "result_resources": result_resources}


def main() -> None:
    adapter = Adapter.from_config()  # dry-run + generic-r4 + json-envelope + capture

    print("Available plugins:", registry.all_plugins())
    print()
    print("=" * 70)
    print("STEP 1 — Inbound 360X Referral Request (PCC-55)  ->  ODE Native FHIR")
    print("=" * 70)
    envelope = build_inbound_envelope()
    with open(os.path.join(HERE, "inbound_pcc55.json"), "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, indent=2)
    inbound = adapter.handle_inbound(envelope)
    print(f"Referral ID:        {inbound['referral_id']}")
    print(f"ODE Native Task:    {inbound['task_id']}  (status: requested)")
    print(f"ServiceRequest:     {inbound['service_request_id']}")
    print("\nResources created in the transaction Bundle:")
    for e in inbound["bundle"]["entry"]:
        r = e["resource"]
        label = r.get("code", {}).get("text") or r.get("resourceType")
        print(f"  - {r['resourceType']:<20} {label}")

    print("\n" + "=" * 70)
    print("STEP 2 — Dental completion  ->  Outbound 360X Referral Outcome (PCC-57)")
    print("=" * 70)
    completion = simulated_completion()
    outbound = adapter.handle_task_event(
        completion["task"], result_resources=completion["result_resources"],
        patient=completion["patient"])
    print(f"Emitted transaction: {outbound['transaction']}")
    print(f"v2 message type:     {hl7v2.TX_MESSAGE_TYPE[outbound['transaction']]}")
    print("\nLOSS NOTES (dental data carried as narrative only on the 360X path):")
    for note in outbound["loss_notes"]:
        print(f"  ! {note}")
    print("\nOutbound C-CDA Consultation Note (Referral Outcome):\n")
    print(outbound["packaged"]["documents"][0]["content"])

    print("=" * 70)
    print("Wrote samples/inbound_pcc55.json — try the HTTP API:")
    print("  uvicorn ode_adapter.app:app --reload")
    print("  curl -s -X POST localhost:8000/360x/inbound \\")
    print("       -H 'Content-Type: application/json' \\")
    print("       -d @samples/inbound_pcc55.json | python -m json.tool")
    print("=" * 70)


if __name__ == "__main__":
    main()
