"""Unit tests for the Medplum FHIR backend plugin.

Mocks httpx so no live server is required. Covers the three Medplum-specific
behaviors the plugin absorbs: OAuth2 token caching + 401 refresh/retry, the
Medplum-safe find_by_referral query construction (no wildcard includes, identifier
only, merged/deduped), auth-header presence on writes, and dry-run parity with the
generic backend.

    pytest tests/test_medplum.py
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import ode_adapter.plugins  # noqa: E402,F401
from ode_adapter import config, registry  # noqa: E402
from ode_adapter.plugins.fhir.medplum import MedplumBackend  # noqa: E402

BASE = "https://example.medplum.com/fhir/R4"
IDENT = f"{config.SYS_REFERRAL_ID}|REF-1"


# --------------------------- httpx fake plumbing --------------------------- #
class FakeResponse:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeClient:
    """Records every request and replays a queue of responses per URL matcher.

    `handler(method, url, kwargs) -> FakeResponse` is supplied per test.
    """

    instances: list["FakeClient"] = []

    def __init__(self, handler, *args, **kwargs):
        self.handler = handler
        self.calls: list[dict] = []
        FakeClient.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def _record(self, method, url, **kwargs):
        call = {"method": method, "url": url, **kwargs}
        self.calls.append(call)
        return self.handler(method, url, kwargs)

    def request(self, method, url, **kwargs):
        return self._record(method, url, **kwargs)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._record("PATCH", url, **kwargs)

    def get(self, url, **kwargs):
        return self._record("GET", url, **kwargs)


def install_fake_httpx(monkeypatch, handler):
    """Patch httpx.Client (used via lazy `import httpx`) with FakeClient. Returns
    the shared call log across all client instances created during the test."""
    import httpx

    FakeClient.instances = []
    monkeypatch.setattr(
        httpx, "Client",
        lambda *a, **k: FakeClient(handler, *a, **k))

    def all_calls():
        return [c for inst in FakeClient.instances for c in inst.calls]

    return all_calls


def _token_response(token="tok-1", expires_in=3600):
    return FakeResponse(200, {"access_token": token, "expires_in": expires_in})


def _backend():
    return MedplumBackend(base_url=BASE, dry_run=False,
                          client_id="cid", client_secret="secret")


# ------------------------------- token url --------------------------------- #
def test_derives_token_url_from_origin():
    b = _backend()
    assert b.token_url == "https://example.medplum.com/oauth2/token"


def test_explicit_token_url_wins():
    b = MedplumBackend(base_url=BASE, dry_run=False, client_id="c",
                       client_secret="s", token_url="https://auth.example/token")
    assert b.token_url == "https://auth.example/token"


# ------------------------------ token caching ------------------------------ #
def test_token_is_cached_across_calls(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"resourceType": "Task", "id": "t1"})

    all_calls = install_fake_httpx(monkeypatch, handler)
    b = _backend()
    b.get_task("t1")
    b.get_task("t1")

    token_posts = [c for c in all_calls()
                   if c["url"].endswith("/oauth2/token")]
    assert len(token_posts) == 1  # second call reused the cached token


def test_token_request_is_form_encoded_and_has_grant(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"resourceType": "Task", "id": "t1"})

    all_calls = install_fake_httpx(monkeypatch, handler)
    _backend().get_task("t1")

    post = next(c for c in all_calls() if c["url"].endswith("/oauth2/token"))
    assert post["data"]["grant_type"] == "client_credentials"
    assert post["data"]["client_id"] == "cid"
    assert post["data"]["client_secret"] == "secret"
    assert post["headers"]["Content-Type"] == "application/x-www-form-urlencoded"


# --------------------------- 401 refresh + retry --------------------------- #
def test_401_refreshes_token_once_and_retries(monkeypatch):
    state = {"fhir_calls": 0, "tokens_issued": 0}

    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            state["tokens_issued"] += 1
            return _token_response(token=f"tok-{state['tokens_issued']}")
        state["fhir_calls"] += 1
        if state["fhir_calls"] == 1:
            return FakeResponse(401)
        return FakeResponse(200, {"resourceType": "Task", "id": "t1"})

    all_calls = install_fake_httpx(monkeypatch, handler)
    b = _backend()
    result = b.get_task("t1")

    assert result == {"resourceType": "Task", "id": "t1"}
    assert state["fhir_calls"] == 2  # original + one retry
    assert state["tokens_issued"] == 2  # initial token + forced refresh
    # Retry carried the refreshed bearer token.
    fhir_calls = [c for c in all_calls() if "/Task/t1" in c["url"]]
    assert fhir_calls[-1]["headers"]["Authorization"] == "Bearer tok-2"


def test_401_retry_does_not_loop_forever(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(401)  # always unauthorized

    install_fake_httpx(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="HTTP 401"):
        _backend().get_task("t1")


# --------------------------- find_by_referral ------------------------------ #
def test_find_by_referral_query_construction(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        if url.endswith("/Task"):
            return FakeResponse(200, {"entry": [
                {"resource": {"resourceType": "Task", "id": "t1"}},
                {"resource": {"resourceType": "Patient", "id": "p1"}},
                # focus ServiceRequest pulled in via _include=Task:focus
                {"resource": {"resourceType": "ServiceRequest", "id": "sr1"}},
            ]})
        if url.endswith("/ServiceRequest"):
            return FakeResponse(200, {"entry": [
                {"resource": {"resourceType": "ServiceRequest", "id": "sr1"}},
                {"resource": {"resourceType": "Patient", "id": "p1"}},  # dup
            ]})
        return FakeResponse(200, {})

    all_calls = install_fake_httpx(monkeypatch, handler)
    results = _backend().find_by_referral("REF-1")

    searches = [c for c in all_calls()
                if c["url"].endswith(("/Task", "/ServiceRequest"))]
    assert len(searches) == 2
    for c in searches:
        params = c["params"]
        # identifier-only, no referral-id param, no wildcard includes/revincludes
        assert params["identifier"] == IDENT
        assert "referral-id" not in params
        assert "_revinclude" not in params
        assert params["_include"] != "*"
    task_search = next(c for c in searches if c["url"].endswith("/Task"))
    sr_search = next(c for c in searches if c["url"].endswith("/ServiceRequest"))
    # Task include is a repeated param (list), carrying both focus and patient.
    assert task_search["params"]["_include"] == ["Task:focus", "Task:patient"]
    assert sr_search["params"]["_include"] == "ServiceRequest:subject"

    # merged across both searches and deduped by resourceType/id — the focus
    # ServiceRequest included by the Task search is deduped against the SR search.
    keys = {(r["resourceType"], r["id"]) for r in results}
    assert keys == {("Task", "t1"), ("Patient", "p1"), ("ServiceRequest", "sr1")}
    assert len(results) == 3


def test_find_by_referral_sends_bearer(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"entry": []})

    all_calls = install_fake_httpx(monkeypatch, handler)
    _backend().find_by_referral("REF-1")

    searches = [c for c in all_calls()
                if c["url"].endswith(("/Task", "/ServiceRequest"))]
    assert searches and all(
        c["headers"]["Authorization"] == "Bearer tok-1" for c in searches)


# ------------------------------ auth on writes ----------------------------- #
def test_submit_referral_bundle_sends_bearer_and_posts_to_base(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"resourceType": "Bundle",
                                  "type": "transaction-response"})

    all_calls = install_fake_httpx(monkeypatch, handler)
    bundle = {"resourceType": "Bundle", "type": "transaction", "entry": []}
    _backend().submit_referral_bundle(bundle)

    post = next(c for c in all_calls()
                if c["method"] == "POST" and c["url"] == BASE)
    assert post["headers"]["Authorization"] == "Bearer tok-1"
    assert post["headers"]["Content-Type"] == "application/fhir+json"
    assert post["json"] == bundle  # pure transport: bundle unchanged


def test_update_task_status_uses_json_patch_with_bearer(monkeypatch):
    def handler(method, url, kwargs):
        if url.endswith("/oauth2/token"):
            return _token_response()
        return FakeResponse(200, {"resourceType": "Task", "id": "t1",
                                  "status": "cancelled"})

    all_calls = install_fake_httpx(monkeypatch, handler)
    _backend().update_task_status("t1", "cancelled", reason="withdrawn")

    patch = next(c for c in all_calls()
                 if c["method"] == "PATCH" and c["url"].endswith("/Task/t1"))
    assert patch["headers"]["Authorization"] == "Bearer tok-1"
    assert patch["headers"]["Content-Type"] == "application/json-patch+json"
    assert patch["json"][0] == {"op": "replace", "path": "/status",
                                "value": "cancelled"}


# ------------------------------- dry-run ----------------------------------- #
def test_dry_run_makes_no_network_calls(monkeypatch):
    def handler(method, url, kwargs):
        raise AssertionError("dry-run must not hit the network")

    install_fake_httpx(monkeypatch, handler)
    b = MedplumBackend(base_url=BASE, dry_run=True,
                       client_id="c", client_secret="s")
    bundle = {"resourceType": "Bundle", "type": "transaction",
              "entry": [{"resource": {"resourceType": "Task"}}]}
    echoed = b.submit_referral_bundle(bundle)
    assert echoed["type"] == "transaction-response"
    assert b.update_task_status("t1", "cancelled")["status"] == "cancelled"
    assert b.update_request_status("sr1", "revoked")["status"] == "revoked"
    assert b.get_task("t1")["resourceType"] == "Task"
    assert b.find_by_referral("REF-1") == []


def test_dry_run_parity_with_generic():
    med = MedplumBackend(base_url=BASE, dry_run=True,
                         client_id="c", client_secret="s")
    gen = registry.create("fhir", "generic-r4", base_url=BASE, dry_run=True)
    bundle = {"resourceType": "Bundle", "type": "transaction",
              "entry": [{"resource": {"resourceType": "Task"}}]}
    assert med.submit_referral_bundle(bundle) == gen.submit_referral_bundle(bundle)
    assert (med.update_task_status("t1", "cancelled")
            == gen.update_task_status("t1", "cancelled"))


# ------------------------------- registry ---------------------------------- #
def test_registered_and_creatable():
    assert "medplum" in registry.available("fhir")
    b = registry.create("fhir", "medplum", base_url=BASE, dry_run=True)
    assert isinstance(b, MedplumBackend)
    assert b.name == "medplum"
