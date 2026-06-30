"""Onyx OnyxOS backend — example of a server-specific plugin.

Some OnyxOS configurations load resources via PUT/UPSERT (resource-by-id) rather
than a POST transaction Bundle. This plugin demonstrates the plug point: it reuses
the generic backend but overrides the load strategy.

NOTE: confirm the exact loading method with the OnyxOS team before relying on this
(tracked in the program TODO). The upsert path requires id assignment and
urn:uuid -> Type/id reference rewriting; the implementation below is a starting
point and must be validated against a live OnyxOS server.
"""
from __future__ import annotations

import uuid

from ...config import settings
from ...registry import register
from .generic_r4 import GenericR4Backend


@register("fhir", "onyx")
class OnyxBackend(GenericR4Backend):
    def __init__(self, base_url: str | None = None, dry_run: bool | None = None,
                 load_mode: str = "upsert"):
        super().__init__(base_url=base_url, dry_run=dry_run, load_mode=load_mode)

    def submit_referral_bundle(self, bundle: dict) -> dict:
        if self.dry_run or self.load_mode != "upsert":
            return super().submit_referral_bundle(bundle)
        import httpx
        puts = self._to_upsert_puts(bundle)
        responses = []
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            for url, resource in puts:
                resp = client.put(f"{self.base_url}/{url}", json=resource,
                                  headers={"Content-Type": "application/fhir+json"})
                resp.raise_for_status()
                responses.append({"response": {"status": str(resp.status_code),
                                               "location": url},
                                  "resource": resp.json()})
        return {"resourceType": "Bundle", "type": "transaction-response",
                "entry": responses}

    @staticmethod
    def _to_upsert_puts(bundle: dict) -> list[tuple[str, dict]]:
        """Assign ids and rewrite urn:uuid references to Type/id for PUT-by-id.

        Starting point — validate ordering/reference behavior against OnyxOS.
        """
        idmap: dict[str, str] = {}
        for e in bundle.get("entry", []):
            res = e["resource"]
            rid = res.get("id") or str(uuid.uuid4())
            res["id"] = rid
            ref = f"{res['resourceType']}/{rid}"
            if e.get("fullUrl"):
                idmap[e["fullUrl"]] = ref

        def rewrite(obj):
            if isinstance(obj, dict):
                if set(obj) == {"reference"} and obj["reference"] in idmap:
                    obj["reference"] = idmap[obj["reference"]]
                else:
                    for v in obj.values():
                        rewrite(v)
            elif isinstance(obj, list):
                for v in obj:
                    rewrite(v)

        puts = []
        for e in bundle.get("entry", []):
            res = e["resource"]
            rewrite(res)
            puts.append((f"{res['resourceType']}/{res['id']}", res))
        return puts
