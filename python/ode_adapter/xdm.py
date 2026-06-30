"""Transport Layer 1 — XDM package envelope.

360X transmits over Direct (SMTP) with an XDM/XDR package (ITI-32) that bundles
the HL7 v2 workflow message and one or more C-CDA documents. Building a full
Direct/MIME/XDM stack is out of scope for this reference; instead the adapter
accepts a JSON envelope that a Direct front end (e.g., a Direct HISP, an MDN-aware
SMTP receiver, or a test harness) produces after unpacking the XDM ZIP.

Replace `InboundEnvelope` ingestion with a real Direct/XDM receiver in production.
The mapping logic downstream is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class XdmDocument:
    id: str
    mime_type: str            # e.g. "text/xml" for C-CDA
    content: str              # inline document content (C-CDA XML) for the reference


@dataclass
class InboundEnvelope:
    """Result of unpacking an inbound XDM package from a Direct message."""
    direct_message_id: str
    submission_set_id: str
    sender_direct_address: str
    recipient_direct_address: str
    transaction: str                       # "PCC-55", "PCC-58", ...
    hl7v2: str                             # the workflow v2 message
    documents: list[XdmDocument] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "InboundEnvelope":
        docs = [XdmDocument(**doc) for doc in d.get("documents", [])]
        return cls(
            direct_message_id=d["direct_message_id"],
            submission_set_id=d["submission_set_id"],
            sender_direct_address=d["sender_direct_address"],
            recipient_direct_address=d["recipient_direct_address"],
            transaction=d["transaction"],
            hl7v2=d.get("hl7v2", ""),
            documents=docs,
        )

    def primary_cda(self) -> str | None:
        for doc in self.documents:
            if "xml" in doc.mime_type:
                return doc.content
        return None


@dataclass
class OutboundEnvelope:
    """An outbound 360X package the adapter hands back to the Direct sender."""
    transaction: str
    sender_direct_address: str
    recipient_direct_address: str
    hl7v2: str
    documents: list[XdmDocument] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "transaction": self.transaction,
            "sender_direct_address": self.sender_direct_address,
            "recipient_direct_address": self.recipient_direct_address,
            "hl7v2": self.hl7v2,
            "documents": [doc.__dict__ for doc in self.documents],
        }
