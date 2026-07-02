"""Minimal HL7 v2 handling for the 360X workflow envelope.

360X carries its workflow state in HL7 v2 messages alongside C-CDA documents:
  - OMG^O19  General Clinical Order  -> PCC-55 Referral Request, PCC-57 Outcome,
                                         PCC-59 Interim Consultation Note (with doc)
  - OSU^O51  Order Status Update     -> PCC-56 Referral Request Status (accept/decline),
                                         PCC-58 Referral Cancellation
  - SIU^S12/S26 Scheduling           -> PCC-60 Appointment, PCC-61 No-Show

This module implements only what the reference flow needs: a tiny pipe-delimited
parser and builders for the messages above. A production adapter should use a
full v2 library and validate against the 360X transaction tables. Field bindings
below are illustrative and MUST be validated against IHE PCC 360X Vol. 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


# Transaction -> v2 message type (validate against 360X Vol. 2 transaction tables)
TX_MESSAGE_TYPE = {
    "PCC-55": "OMG^O19",   # Referral Request
    "PCC-56": "OSU^O51",   # Referral Request Status (accept / decline)
    "PCC-57": "OMG^O19",   # Referral Outcome (with consultation note)
    "PCC-58": "OSU^O51",   # Referral Cancellation
    "PCC-59": "OMG^O19",   # Interim Consultation Note (with note)
    "PCC-60": "SIU^S12",   # Appointment Notification
    "PCC-61": "SIU^S26",   # No-Show Notification
}


@dataclass
class V2Message:
    """A parsed-enough representation of a 360X v2 message."""
    message_type: str                 # e.g. "OSU^O51"
    control_id: str
    referral_id: str | None = None    # placer/referral identifier (the loop key)
    order_status: str | None = None   # for OSU^O51: e.g. "IP" accepted, "CA" cancelled
    fields: dict = field(default_factory=dict)
    raw: str = ""


def parse(raw: str) -> V2Message:
    """Parse the handful of segments the reference flow inspects."""
    segs = [s for s in raw.replace("\n", "\r").split("\r") if s.strip()]
    msg = V2Message(message_type="", control_id="", raw=raw)
    for seg in segs:
        f = seg.split("|")
        sid = f[0]
        if sid == "MSH":
            msg.message_type = f[8] if len(f) > 8 else ""
            msg.control_id = f[9] if len(f) > 9 else ""
        elif sid == "ORC":
            # ORC-2 placer order number used as referral id; ORC-5 order status.
            # Reply content (degraded 360X side): ORC-12 accepting provider,
            # ORC-15 expected-by date, ORC-16 coded status reason.
            if len(f) > 2 and f[2]:
                msg.referral_id = f[2]
            if len(f) > 5 and f[5]:
                msg.order_status = f[5]
            if len(f) > 12 and f[12]:
                msg.fields["accepting_provider"] = f[12]
            if len(f) > 15 and f[15]:
                msg.fields["period_end"] = f[15]
            if len(f) > 16 and f[16]:
                msg.fields["status_reason"] = f[16]
        elif sid == "NTE":
            # NTE-3 comment carries the free-text reply note / reason (degraded).
            if len(f) > 3 and f[3]:
                msg.fields["note"] = f[3]
        elif sid == "ZRF" and len(f) > 1:  # 360X referral-id convention (illustrative)
            msg.referral_id = msg.referral_id or f[1]
        elif sid == "SCH":
            # SIU appointment timing: SCH-11 start, SCH-12 end (illustrative binding).
            if len(f) > 11 and f[11]:
                msg.fields["appointment_start"] = f[11]
            if len(f) > 12 and f[12]:
                msg.fields["appointment_end"] = f[12]
    return msg


def _set_idx(seg: list[str], idx: int, value: str) -> None:
    while len(seg) <= idx:
        seg.append("")
    seg[idx] = value


def build(message_type: str, referral_id: str, order_status: str | None = None,
          control_id: str | None = None, appointment_start: str | None = None,
          *, appointment_end: str | None = None, note: str | None = None,
          accepting_provider: str | None = None, status_reason: str | None = None,
          period_end: str | None = None) -> str:
    """Build a minimal v2 message for the given 360X transaction.

    For SIU scheduling messages (PCC-60/61) an `appointment_start`/`appointment_end`
    (HL7 v2 ts or ISO-8601) is carried in an SCH segment so the inbound bridge can
    build an Appointment.

    Reply content (the degraded 360X side of the crosswalk):
      `accepting_provider` -> ORC-12, `period_end` -> ORC-15, `status_reason` ->
      ORC-16, and `note` -> an NTE segment. Field bindings are illustrative —
      validate against 360X Vol. 2.
    """
    control_id = control_id or f"ADP{_ts()}"
    msh = "|".join([
        "MSH", "^~\\&", "OHIA-360ODE-ADAPTER", "OHIA",
        "EHR", "ORG", _ts(), "", message_type, control_id, "P", "2.5.1",
    ])
    segs = [msh]
    orc_status = order_status or ""
    # ORC-1 order control; map by message
    order_control = "SC" if message_type.startswith("OSU") else "NW"
    orc = ["ORC", order_control, referral_id, "", "", orc_status]
    if accepting_provider:
        _set_idx(orc, 12, accepting_provider)
    if period_end:
        _set_idx(orc, 15, period_end)
    if status_reason:
        _set_idx(orc, 16, status_reason)
    segs.append("|".join(orc))
    if message_type.startswith("SIU"):
        sch = ["SCH"] + [""] * 12
        sch[11] = appointment_start or ""
        sch[12] = appointment_end or ""
        segs.append("|".join(sch))
    if note:
        segs.append("|".join(["NTE", "1", "", note]))
    return "\r".join(segs) + "\r"
