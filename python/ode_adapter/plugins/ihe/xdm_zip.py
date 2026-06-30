"""XDM ZIP codec — parse/produce real IHE XDM packages.

Scaffold for the production codec. An XDM package is a ZIP with:
    INDEX.HTM
    METADATA.XML            (submission set + DocumentEntry metadata, ITI-32)
    IHE_XDM/<subset>/...     (the C-CDA documents and a v2 message)

Implement `unpack` to read METADATA.XML + the v2 message + C-CDA into an
InboundEnvelope, and `pack` to assemble the ZIP for outbound. See program TODO §2.
"""
from __future__ import annotations

from typing import Any

from ...ports import IheCodec
from ...registry import register
from ...xdm import InboundEnvelope, OutboundEnvelope


@register("codec", "xdm-zip")
class XdmZipCodec(IheCodec):
    def unpack(self, raw: bytes | str | dict) -> InboundEnvelope:
        raise NotImplementedError(
            "XDM ZIP unpack not yet implemented — parse METADATA.XML + v2 + C-CDA. "
            "See TODO.md §2.")

    def pack(self, envelope: OutboundEnvelope) -> Any:
        raise NotImplementedError(
            "XDM ZIP pack not yet implemented — assemble INDEX.HTM + METADATA.XML + "
            "IHE_XDM/. See TODO.md §2.")
