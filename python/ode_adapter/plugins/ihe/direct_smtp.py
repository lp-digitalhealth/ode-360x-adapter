"""Direct (SMTP) outbound transport — scaffold for real Direct sending.

Send the packaged 360X message to the recipient Direct address over a HISP, with
S/MIME signing/encryption against configured trust anchors and MDN handling.
See program TODO §2 / §6.
"""
from __future__ import annotations

from typing import Any

from ...ports import IheOutboundTransport
from ...registry import register


@register("transport", "direct")
class DirectTransport(IheOutboundTransport):
    def __init__(self, hisp_endpoint: str | None = None):
        self.hisp_endpoint = hisp_endpoint

    def send(self, packaged: Any) -> Any:
        raise NotImplementedError(
            "Direct/SMTP send not yet implemented — S/MIME + HISP + MDN. "
            "See TODO.md §2.")
