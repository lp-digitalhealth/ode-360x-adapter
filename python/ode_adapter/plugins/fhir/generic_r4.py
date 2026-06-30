"""Generic FHIR R4 backend — works with any conformant R4 server (e.g. HAPI).

Default plugin. In dry-run it echoes the bundle with synthetic ids so the adapter
runs with no live server.
"""
from __future__ import annotations

from ...config import settings
from ...ports import FhirBackend
from ...registry import register


@register("fhir", "generic-r4")
class GenericR4Backend(FhirBackend):
    def __init__(self, base_url: str | None = None, dry_run: bool | None = None,
                 load_mode: str = "transaction"):
        self.base_url = (base_url or settings.ode_native_base_url).rstrip("/")
        self.dry_run = settings.dry_run if dry_run is None else dry_run
        self.load_mode = load_mode  # "transaction" (POST bundle) by default

    # -- writes -------------------------------------------------------------- #
    def submit_referral_bundle(self, bundle: dict) -> dict:
        if self.dry_run:
            return self._echo(bundle)
        import httpx
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.post(self.base_url, json=bundle,
                               headers={"Content-Type": "application/fhir+json"})
            resp.raise_for_status()
            return resp.json()

    def update_task_status(self, task_id: str, status: str,
                           reason: str | None = None) -> dict:
        if self.dry_run:
            return {"resourceType": "Task", "id": task_id, "status": status}
        import httpx
        patch = [{"op": "replace", "path": "/status", "value": status}]
        if reason:
            patch.append({"op": "add", "path": "/statusReason",
                          "value": {"text": reason}})
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.patch(f"{self.base_url}/Task/{task_id}", json=patch,
                               headers={"Content-Type": "application/json-patch+json"})
            resp.raise_for_status()
            return resp.json()

    # -- reads --------------------------------------------------------------- #
    def get_task(self, task_id: str) -> dict:
        if self.dry_run:
            return {"resourceType": "Task", "id": task_id, "status": "requested"}
        import httpx
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.get(f"{self.base_url}/Task/{task_id}")
            resp.raise_for_status()
            return resp.json()

    # -- helpers ------------------------------------------------------------- #
    @staticmethod
    def _echo(bundle: dict) -> dict:
        entries = []
        for i, e in enumerate(bundle.get("entry", [])):
            res = dict(e["resource"])
            rtype = res["resourceType"]
            res["id"] = f"{rtype.lower()}-{i + 1}"
            entries.append({"response": {"status": "201 Created",
                                         "location": f"{rtype}/{res['id']}"},
                            "resource": res})
        return {"resourceType": "Bundle", "type": "transaction-response",
                "entry": entries}
