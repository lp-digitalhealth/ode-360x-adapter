"""HTTP outbound transport — POST the packaged 360X message to a receiver URL.

This is the companion to the stub 360X server (tools/stub_360x_server.py): it sends
each outbound envelope over HTTP so the adapter's outbound path can be exercised
end-to-end without real Direct/HISP infrastructure. In production you would use the
`direct` transport instead (S/MIME + HISP); this one is for testing and Connectathon.
"""
from __future__ import annotations

from typing import Any

from ...config import settings
from ...ports import IheOutboundTransport
from ...registry import register


@register("transport", "http")
class HttpTransport(IheOutboundTransport):
    def __init__(self, url: str | None = None):
        self.url = url or settings.ihe_outbound_url

    def send(self, packaged: Any) -> Any:
        import httpx
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.post(self.url, json=packaged,
                               headers={"Content-Type": "application/json"})
            resp.raise_for_status()
            return resp.json() if resp.content else {"status": resp.status_code}
