"""Generic FHIR R4 backend — works with any conformant R4 server (e.g. HAPI).

Default plugin. In dry-run it echoes the bundle with synthetic ids so the adapter
runs with no live server.
"""
from __future__ import annotations

from ... import config, cow
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
                           reason: str | None = None,
                           business_status: str | None = None,
                           outputs: list[dict] | None = None,
                           *, owner: str | dict | None = None,
                           status_reason: dict | None = None,
                           note: str | None = None,
                           period_end: str | None = None) -> dict:
        status_reason_val = status_reason or ({"text": reason} if reason else None)
        owner_val = ({"reference": owner} if isinstance(owner, str) else owner)
        if self.dry_run:
            task = {"resourceType": "Task", "id": task_id, "status": status}
            if status_reason_val:
                task["statusReason"] = status_reason_val
            if business_status:
                task["businessStatus"] = cow.business_status_concept(business_status)
            if owner_val:
                task["owner"] = owner_val
            if note:
                task["note"] = [{"text": note}]
            if period_end:
                task["restriction"] = {"period": {"end": period_end}}
            if outputs:
                task["output"] = outputs
            return task
        import httpx
        patch = [{"op": "replace", "path": "/status", "value": status}]
        if status_reason_val:
            patch.append({"op": "add", "path": "/statusReason",
                          "value": status_reason_val})
        if business_status:
            patch.append({"op": "add", "path": "/businessStatus",
                          "value": cow.business_status_concept(business_status)})
        if owner_val:
            patch.append({"op": "add", "path": "/owner", "value": owner_val})
        if note:
            patch.append({"op": "add", "path": "/note", "value": [{"text": note}]})
        if period_end:
            patch.append({"op": "add", "path": "/restriction",
                          "value": {"period": {"end": period_end}}})
        if outputs:
            patch.append({"op": "add", "path": "/output", "value": outputs})
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.patch(f"{self.base_url}/Task/{task_id}", json=patch,
                               headers={"Content-Type": "application/json-patch+json"})
            resp.raise_for_status()
            return resp.json()

    def update_request_status(self, request_id: str, status: str,
                              reason: str | None = None) -> dict:
        if self.dry_run:
            sr = {"resourceType": "ServiceRequest", "id": request_id, "status": status}
            if reason:
                sr["_status_reason"] = reason
            return sr
        import httpx
        patch = [{"op": "replace", "path": "/status", "value": status}]
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.patch(f"{self.base_url}/ServiceRequest/{request_id}",
                               json=patch,
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

    def find_by_referral(self, referral_id: str) -> list[dict]:
        """Live read: the resources the bridge wrote for this referral. In dry-run
        there is no server to query, so the engine uses its per-episode cache."""
        if self.dry_run:
            return []
        import httpx
        ident = f"{config.SYS_REFERRAL_ID}|{referral_id}"
        out: list[dict] = []
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            for rtype in ("Task", "ServiceRequest"):
                # `referral-id` is the contract's token search param; fall back to the
                # base `identifier` param for servers that only index that.
                resp = client.get(f"{self.base_url}/{rtype}",
                                  params={"referral-id": ident, "identifier": ident,
                                          "_include": "*", "_revinclude": "*"})
                if resp.status_code == 200:
                    out.extend(e["resource"] for e in resp.json().get("entry", []))
        return out

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
