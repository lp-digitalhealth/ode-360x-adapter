"""Medplum backend — server-specific adaptation of the generic R4 backend.

Verified against Medplum 5.1.x. Medplum differs from a vanilla HAPI server in three
ways this plugin absorbs; everything else reuses :class:`GenericR4Backend`.

1. AUTH. Medplum requires OAuth2 SMART Backend Services auth on every request. This
   plugin runs the ``client_credentials`` grant (POST form-encoded client_id/secret
   to the token endpoint), caches the bearer token in-process using ``expires_in``
   (refreshing 60s early), sends ``Authorization: Bearer`` on every call, and on a
   401 refreshes once and retries the request once. The secret and token are never
   logged.

2. STRICT SEARCH. Medplum does not silently ignore unknown search params (so the
   contract's ``referral-id`` param 400s and the ``identifier`` fallback never runs)
   and rejects ``_include=*``. :meth:`find_by_referral` therefore searches by
   ``identifier`` only, with explicit (non-wildcard) includes.

3. WHAT ALREADY WORKS. POSTing the transaction Bundle to the FHIR base URL and the
   JSON-Patch status updates work as-is against Medplum; those methods mirror the
   generic backend and only add the auth header.

This is a pure transport adapter: it does not filter or transform Bundle contents.
The receiving Medplum server's access policy governs what it accepts.
"""
from __future__ import annotations

import time
from urllib.parse import urlsplit

from ... import config, cow
from ...config import settings
from ...registry import register
from .generic_r4 import GenericR4Backend


@register("fhir", "medplum")
class MedplumBackend(GenericR4Backend):
    def __init__(self, base_url: str | None = None, dry_run: bool | None = None,
                 load_mode: str = "transaction",
                 client_id: str | None = None, client_secret: str | None = None,
                 token_url: str | None = None):
        super().__init__(base_url=base_url, dry_run=dry_run, load_mode=load_mode)
        self.client_id = client_id if client_id is not None else settings.medplum_client_id
        self.client_secret = (client_secret if client_secret is not None
                              else settings.medplum_client_secret)
        self.token_url = (token_url or settings.medplum_token_url
                         or self._derive_token_url(self.base_url))
        # In-process token cache. Never logged.
        self._token: str | None = None
        self._token_expiry: float = 0.0

    # -- auth ---------------------------------------------------------------- #
    @staticmethod
    def _derive_token_url(base_url: str) -> str:
        """<origin>/oauth2/token — the FHIR base is <origin>/fhir/R4, so the origin
        (scheme + host) is what carries the token endpoint."""
        parts = urlsplit(base_url)
        return f"{parts.scheme}://{parts.netloc}/oauth2/token"

    def _get_token(self, *, force: bool = False) -> str:
        """Return a cached bearer token, fetching a new one via client_credentials
        when missing, expired (60s early), or forced (after a 401)."""
        if not force and self._token and time.monotonic() < self._token_expiry:
            return self._token
        import httpx
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.post(
                self.token_url,
                data={"grant_type": "client_credentials",
                      "client_id": self.client_id,
                      "client_secret": self.client_secret},
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp.raise_for_status()
            payload = resp.json()
        self._token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 3600))
        self._token_expiry = time.monotonic() + max(expires_in - 60, 0)
        return self._token

    def _send(self, method: str, url: str, **kwargs):
        """Issue an authenticated request; on 401 refresh the token once and retry
        the request once."""
        import httpx
        headers = kwargs.pop("headers", None) or {}
        with httpx.Client(timeout=settings.request_timeout_seconds) as client:
            resp = client.request(
                method, url,
                headers={"Authorization": f"Bearer {self._get_token()}", **headers},
                **kwargs)
            if resp.status_code == 401:
                resp = client.request(
                    method, url,
                    headers={"Authorization": f"Bearer {self._get_token(force=True)}",
                             **headers},
                    **kwargs)
            resp.raise_for_status()
            return resp

    # -- writes (mirror generic; add auth) ----------------------------------- #
    def submit_referral_bundle(self, bundle: dict) -> dict:
        if self.dry_run:
            return self._echo(bundle)
        resp = self._send("POST", self.base_url, json=bundle,
                          headers={"Content-Type": "application/fhir+json"})
        return resp.json()

    def update_task_status(self, task_id: str, status: str,
                           reason: str | None = None,
                           business_status: str | None = None,
                           outputs: list[dict] | None = None,
                           *, owner: str | dict | None = None,
                           status_reason: dict | None = None,
                           note: str | None = None,
                           period_end: str | None = None) -> dict:
        if self.dry_run:
            return super().update_task_status(
                task_id, status, reason, business_status, outputs, owner=owner,
                status_reason=status_reason, note=note, period_end=period_end)
        status_reason_val = status_reason or ({"text": reason} if reason else None)
        owner_val = ({"reference": owner} if isinstance(owner, str) else owner)
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
        resp = self._send("PATCH", f"{self.base_url}/Task/{task_id}", json=patch,
                          headers={"Content-Type": "application/json-patch+json"})
        return resp.json()

    def update_request_status(self, request_id: str, status: str,
                              reason: str | None = None) -> dict:
        if self.dry_run:
            return super().update_request_status(request_id, status, reason)
        patch = [{"op": "replace", "path": "/status", "value": status}]
        resp = self._send("PATCH", f"{self.base_url}/ServiceRequest/{request_id}",
                          json=patch,
                          headers={"Content-Type": "application/json-patch+json"})
        return resp.json()

    # -- reads --------------------------------------------------------------- #
    def get_task(self, task_id: str) -> dict:
        if self.dry_run:
            return super().get_task(task_id)
        resp = self._send("GET", f"{self.base_url}/Task/{task_id}")
        return resp.json()

    def find_by_referral(self, referral_id: str) -> list[dict]:
        """Live read of the resources written for this referral. Medplum-safe:
        searches by `identifier` only (no `referral-id` param, which Medplum 400s)
        and uses explicit, non-wildcard `_include`s (Medplum rejects `_include=*`).
        Entries are merged across resource types and deduped by resourceType/id."""
        if self.dry_run:
            return []
        ident = f"{config.SYS_REFERRAL_ID}|{referral_id}"
        # `_include` values must be sent as repeated query params, not a single
        # comma-joined value; httpx serializes a list into repeated params.
        searches = (
            ("Task", {"identifier": ident,
                     "_include": ["Task:focus", "Task:patient"]}),
            ("ServiceRequest", {"identifier": ident,
                                "_include": "ServiceRequest:subject"}),
        )
        seen: set[tuple[str, str]] = set()
        out: list[dict] = []
        for rtype, params in searches:
            resp = self._send("GET", f"{self.base_url}/{rtype}", params=params)
            for entry in resp.json().get("entry", []):
                res = entry["resource"]
                key = (res.get("resourceType", ""), res.get("id", ""))
                if key in seen:
                    continue
                seen.add(key)
                out.append(res)
        return out
