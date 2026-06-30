"""JSON-envelope codec + capture transport — the default, dependency-free pair.

JsonEnvelopeCodec assumes a Direct/XDM front end has already unpacked the package
into the JSON envelope shape (see xdm.InboundEnvelope). CaptureTransport records
outbound messages in memory, which is what the demo and tests consume. Swap these
for xdm-zip + direct in a deployment.
"""
from __future__ import annotations

import json
from typing import Any

from ...ports import IheCodec, IheOutboundTransport
from ...registry import register
from ...xdm import InboundEnvelope, OutboundEnvelope


@register("codec", "json-envelope")
class JsonEnvelopeCodec(IheCodec):
    def unpack(self, raw: bytes | str | dict) -> InboundEnvelope:
        if isinstance(raw, (bytes, str)):
            raw = json.loads(raw)
        return InboundEnvelope.from_dict(raw)

    def pack(self, envelope: OutboundEnvelope) -> dict:
        return envelope.to_dict()


@register("transport", "capture")
class CaptureTransport(IheOutboundTransport):
    """Records outbound messages instead of sending them (reference/testing)."""

    def __init__(self):
        self.sent: list[Any] = []

    def send(self, packaged: Any) -> Any:
        self.sent.append(packaged)
        return packaged
