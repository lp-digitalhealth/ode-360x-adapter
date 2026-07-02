"""Layer 2 — Workflow state mapping (the clean, ~1:1 layer).

Maps 360X transactions to/from the COW/ODE `Task` state machine. The adapter sits
on the DENTAL edge: the medical EHR is the 360X Referral Initiator, the dental
system (ODE Native) is the Referral Recipient / Fulfiller.

Direction of travel:
  Inbound  (medical -> adapter -> dental):  PCC-55 Referral Request, PCC-58 Cancel
  Outbound (dental  -> adapter -> medical): PCC-56 Status (accept/decline),
                                            PCC-59 Interim Note, PCC-57 Outcome,
                                            PCC-60 Appointment, PCC-61 No-Show
"""
from __future__ import annotations

from dataclasses import dataclass


# Inbound 360X transaction -> resulting ODE Task.status
INBOUND_TX_TO_TASK_STATUS = {
    "PCC-55": "requested",     # new referral -> create Task(requested) + ServiceRequest
    "PCC-58": "cancelled",     # initiator cancels -> revoke
}


@dataclass
class OutboundDecision:
    transaction: str            # 360X transaction code to emit
    order_status: str | None    # OSU^O51 status value, if applicable
    needs_document: bool        # whether a C-CDA document accompanies the message
    document_type: str | None   # LOINC doc type when needs_document


def task_to_360x(task_status: str, *, has_result: bool = False,
                 interim: bool = False) -> OutboundDecision | None:
    """Given an ODE Task state change, decide which 360X transaction to emit.

    `interim` distinguishes an in-progress *update with a note* (PCC-59) from a
    plain accept (PCC-56). `has_result` indicates a completion carries an outcome
    document (PCC-57).
    """
    from .config import DOC_CONSULTATION_NOTE

    if task_status in ("accepted", "in-progress") and interim:
        return OutboundDecision("PCC-59", None, True, DOC_CONSULTATION_NOTE)
    if task_status in ("accepted", "in-progress"):
        return OutboundDecision("PCC-56", "IP", False, None)      # accept
    if task_status in ("rejected", "failed"):
        return OutboundDecision("PCC-56", "CA", False, None)      # decline
    if task_status == "cancelled":
        return OutboundDecision("PCC-58", "CA", False, None)
    if task_status == "completed":
        return OutboundDecision("PCC-57", "CM", True, DOC_CONSULTATION_NOTE)
    return None


def appointment_event(no_show: bool = False) -> OutboundDecision:
    """PCC-60 Appointment Notification / PCC-61 No-Show Notification."""
    if no_show:
        return OutboundDecision("PCC-61", None, False, None)
    return OutboundDecision("PCC-60", None, False, None)


# --------------------------------------------------------------------------- #
# Reply ingestion (360X -> COW/FHIR) — the mirror direction.
#
# When *this* side initiated the referral, the peer's reply transactions arrive
# inbound and must be projected onto the COW Task/Request state. This is the
# direction-agnostic core: the same table serves the dental harness (medical peer
# replies) and the medical harness (dental peer replies). See the crosswalk.
# --------------------------------------------------------------------------- #
@dataclass
class ReplyDecision:
    task_status: str            # resulting Task.status
    business_status: str        # COW dental business-status code
    request_status: str | None  # ServiceRequest.status to apply, if it changes
    needs_document: bool        # whether a C-CDA consultation note is expected
    kind: str                   # "status" | "outcome" | "interim" | "appointment" | "noshow"


def reply_to_fhir(transaction: str, order_status: str | None = None
                  ) -> ReplyDecision | None:
    """Map an inbound reply 360X transaction to a COW Task/Request state change."""
    if transaction == "PCC-56":
        if (order_status or "").upper() == "CA":
            return ReplyDecision("rejected", "declined", "revoked", False, "status")
        return ReplyDecision("accepted", "accepted", "active", False, "status")
    if transaction == "PCC-57":
        return ReplyDecision("completed", "outcome-final", "completed", True, "outcome")
    if transaction == "PCC-59":
        return ReplyDecision("in-progress", "interim-results", "active", True, "interim")
    if transaction == "PCC-60":
        return ReplyDecision("in-progress", "appointment-booked", "active", False,
                             "appointment")
    if transaction == "PCC-61":
        return ReplyDecision("in-progress", "appointment-noshow", "active", False,
                             "noshow")
    return None


REPLY_TRANSACTIONS = ("PCC-56", "PCC-57", "PCC-59", "PCC-60", "PCC-61")
