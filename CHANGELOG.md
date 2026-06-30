# Changelog

## 0.2.0 — stub peer, attachments contract, docs, tests
- **Two-way stub 360X peer** (`python/tools/`): receiver core (`stub_360x_core.py`)
  + sender (`stub_360x_sender.py`) + thin FastAPI server (`stub_360x_server.py`).
  Drives the adapter from the medical side (PCC-55/58) and receives its outbound
  messages (PCC-56/57/58/59). CLI included.
- **`http` outbound transport** plugin so the adapter can POST outbound 360X to a
  receiver (the stub or a real endpoint).
- **Attachments contract** (`spec/mapping/attachments.md`): supporting images via
  embed vs. bridge-hosted capability link; the three physician/dentist cases;
  DocumentReference (provider-to-provider), DICOM/WADO-RS, security model.
- **Tests** (`python/tests/`): 45 passing — adapter recheck (26), stub receiver (10),
  stub sender (9), driven by real adapter output.
- **Docs** (`docs/`): free FHIR server comparison (`fhir-servers.md`) and a
  beginner-friendly HAPI/Firely run guide (`running-a-fhir-server.md`).

## 0.1.0 — initial drop
- Monorepo layout: `spec/` (contract), `python/` (reference), `java/` + `dotnet/`
  (skeletons), `docker/`, `wiki/`, `PROGRAM-PLAN.md`.
- `spec/` contract: ports, 360X↔Task + C-CDA↔FHIR mappings, loss profile,
  conformance checklist, UC01 + UC03 use-case specs.
- Python reference: ports-and-adapters core; PCC-55 inbound + PCC-57 outbound
  working end-to-end; plugins generic-r4/onyx and json-envelope/capture.
- Containers (HAPI + Python adapter), CI scaffold, CONTRIBUTING.
