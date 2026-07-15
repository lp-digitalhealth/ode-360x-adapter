# 360/ODE Adapter — OHIA Reference Implementation

A bridging **edge server** between **IHE 360X** (closed-loop referral over Direct /
XDM / HL7 v2 / C-CDA) and **ODE Native** (FHIR R4 realization of the Clinical Order
Workflows framework, with oral-health profiles).

The adapter lets a medical EHR keep speaking 360X end to end while the dental system
speaks only modern FHIR. It is the implementation of the "Medical provider ⟷ dentist"
bridge described in the ODE architecture and the PSS-2779 scope statement.

> **Status: reference / Connectathon. Expect refactoring.** This implements the core
> closed-loop happy path with honest stubs at the edges; see *What is implemented vs.
> stubbed*. It is structured so the mapping logic is the readable centerpiece and the
> transport plumbing is swappable.

> **Plug and play.** The FHIR server and the IHE transport are swappable plugins
> behind three ports — change a config value, not code, to move between HAPI and
> OnyxOS, or between a JSON test envelope and real Direct/XDM. See **ARCHITECTURE.md**.

---

## Quick start

```bash
# zero external dependencies needed for the demo (pure stdlib)
python samples/demo.py
```

The demo runs a full cycle with no live FHIR server: inbound **PCC-55 Referral
Request** (C-CDA) → FHIR Bundle on ODE Native → dental completion → outbound
**PCC-57 Referral Outcome** (C-CDA), and prints the **loss notes** showing which
dental data could only be carried as narrative.

To run the HTTP service:

```bash
pip install -e ".[server,fhir]"
uvicorn ode_adapter.app:app --reload
curl -s -X POST localhost:8000/360x/inbound \
     -H 'Content-Type: application/json' \
     -d @samples/inbound_pcc55.json | python -m json.tool
curl -s localhost:8000/plugins | python -m json.tool   # see swappable plugins
```

By default `ODE_ADAPTER_DRY_RUN=true` (the ODE client echoes bundles instead of
POSTing). Point at a real FHIR server and disable dry-run:

```bash
export ODE_ADAPTER_DRY_RUN=false
export ODE_ADAPTER_ODE_BASE_URL=https://your-ode-native.example.org/fhir
```

### FHIR backend plugins

Select the backend with `ODE_ADAPTER_FHIR_BACKEND` (default `generic-r4`).

| Backend | `ODE_ADAPTER_FHIR_BACKEND` | Notes | Extra env vars |
|---|---|---|---|
| Generic R4 | `generic-r4` | any conformant R4 server (e.g. HAPI); default | — |
| OnyxOS | `onyx` | server-specific PUT/upsert loading | — |
| Medplum | `medplum` | Medplum 5.1.x; OAuth2 SMART Backend Services auth + Medplum-safe search | `ODE_ADAPTER_MEDPLUM_CLIENT_ID`, `ODE_ADAPTER_MEDPLUM_CLIENT_SECRET`, `ODE_ADAPTER_MEDPLUM_TOKEN_URL` (optional; defaults to `<origin>/oauth2/token`) |

The Medplum backend runs the `client_credentials` grant, caches the bearer token
in-process (refreshing 60s early), retries once on a 401, and sends
`Authorization: Bearer` on every call. The client secret and token are never logged.
It is a pure transport adapter — Bundle contents are not filtered or transformed; the
server's access policy governs what it accepts.

```bash
export ODE_ADAPTER_FHIR_BACKEND=medplum
export ODE_ADAPTER_ODE_BASE_URL=https://your-project.medplum.com/fhir/R4
export ODE_ADAPTER_MEDPLUM_CLIENT_ID=...        # SMART Backend Services client
export ODE_ADAPTER_MEDPLUM_CLIENT_SECRET=...    # keep out of logs / VCS
```

---

## Architecture

Three translation layers, in increasing order of difficulty. **Orientation: the
adapter sits on the dental edge** — the medical EHR is the 360X Referral Initiator;
the dental system (ODE Native) is the Referral Recipient / COW Fulfiller.

| Layer | Module | Job | Difficulty |
|---|---|---|---|
| 1 — Transport bridge | `xdm.py`, `hl7v2.py`, `plugins/ihe/*` | Direct/XDM/v2 ↔ FHIR REST | mechanical |
| 2 — Workflow state | `state_machine.py` | 360X transactions ↔ `Task` state machine | clean ~1:1 |
| 3 — Content transform | `ccda_to_fhir.py`, `fhir_to_ccda.py` | C-CDA ↔ FHIR (+ dental) | degraded (the hard part) |
| Ports / plugins | `ports.py`, `registry.py`, `plugins/*` | swappable FHIR backend + IHE transport | — |
| Correlation / directory | `stores.py` | episode state, addressing | stateful |
| Orchestration | `engine.py` | ties it together, both directions | — |

### Direction of travel

- **Inbound** (medical → adapter → dental): `PCC-55` Referral Request, `PCC-58` Cancellation
- **Outbound** (dental → adapter → medical): `PCC-56` Status (accept/decline),
  `PCC-59` Interim Note, `PCC-57` Outcome, `PCC-60` Appointment, `PCC-61` No-Show

---

## The two faces (HTTP API)

| Endpoint | Stands in for | Purpose |
|---|---|---|
| `POST /360x/inbound` | a Direct/XDM receiver | accept an unpacked XDM envelope, drive ODE Native |
| `POST /ode/task-event` | a FHIR Subscription callback | accept an ODE Task change, emit outbound 360X |
| `GET /episodes` | — | inspect the correlation store |
| `GET /healthz` | — | liveness |

The `InboundEnvelope` is the unpacked output of a Direct message (v2 + C-CDA);
a production deployment replaces the HTTP endpoint with a Direct/HISP front end and
leaves the mapping logic unchanged.

---

## Layer 2 — Workflow state mapping (clean ~1:1)

| 360X transaction | v2 message | ODE `Task` interaction | `Task.status` |
|---|---|---|---|
| Referral Request `PCC-55` | `OMG^O19` | create Task + ServiceRequest | `requested` |
| Referral Request Status `PCC-56` (accept) | `OSU^O51` | Fulfiller accepts | `accepted`/`in-progress` |
| Referral Request Status `PCC-56` (decline) | `OSU^O51` | Fulfiller rejects | `rejected` |
| Interim Consultation Note `PCC-59` | `OMG^O19` + doc | interim result | `in-progress` |
| Referral Outcome `PCC-57` | `OMG^O19` + doc | final result, close | `completed` |
| Referral Cancellation `PCC-58` | `OSU^O51` | revoke | `cancelled` |
| Appointment Notification `PCC-60` | `SIU^S12` | Appointment event | (unchanged) |
| No-Show Notification `PCC-61` | `SIU^S26` | notification | (unchanged) |

> v2 message-type bindings are confirmed against the IHE PCC 360X supplement
> (Rev. 1.2, 2021-04-14): `OMG^O19`, `OSU^O51`, and `SIU` messages. Field-level
> bindings in `hl7v2.py` are illustrative and must be validated against 360X Vol. 2.

---

## Layer 3 — Content mapping (C-CDA ↔ FHIR)

Documents map onto C-CDA on FHIR document profiles, then decompose into US Core + ODE
resources. Inbound medical content reuses the published **C-CDA ↔ US Core** section
mappings:

| C-CDA section (LOINC) | FHIR resource |
|---|---|
| Problems (11450-4) | `Condition` |
| Medications (10160-0) | `MedicationRequest` |
| Allergies (48765-2) | `AllergyIntolerance` |
| Results (30954-2) | `Observation` / `DiagnosticReport` |
| Procedures (47519-4) | `Procedure` |
| Reason for referral (42349-1) | `ServiceRequest.reasonCode` |
| Plan of treatment (18776-5) | `CarePlan` / `ServiceRequest` |

### The loss profile (the asymmetry that matters)

Inbound medical content bridges cleanly. **Outbound dental content does not** — C-CDA
has no structured slot for it, so `fhir_to_ccda.py` renders it into a flagged
narrative section and emits **loss notes** rather than dropping it:

| Dental data | ODE Native | On the 360X bridge |
|---|---|---|
| CDT procedures (`http://www.ada.org/cdt`) | `Procedure.code` | narrative only |
| Tooth numbering (Universal / FDI ISO 3950) | `bodySite` (ODE ext.) | narrative only |
| Periodontal observations | ODE perio `Observation` | narrative only |
| Radiographs / images | `DocumentReference` / `Media` | attachment, metadata lost |

This asymmetry is the architectural argument for **ODE Native end-to-end**: the
dentist-to-dentist and FHIR-HIE paths avoid the loss entirely.

---

## What is implemented vs. stubbed

**Implemented (and demonstrated by `samples/demo.py`):**
- Inbound `PCC-55` → C-CDA Referral Note parse → FHIR transaction Bundle (Patient,
  Practitioner, Organization, Condition, MedicationRequest, AllergyIntolerance,
  ServiceRequest, Task=requested, Provenance) → submit to ODE Native.
- Outbound `PCC-57` Referral Outcome → C-CDA Consultation Note from Task + result
  resources, with the dental loss profile applied and loss notes surfaced.
- Episode correlation across both directions; tolerant handling of an orphan `PCC-58`.

**Stubbed / extension points (clearly marked in code):**
- `PCC-56` accept/decline, `PCC-59` interim, `PCC-60`/`PCC-61` scheduling — the
  state machine + builders exist; wire them to real ODE Task events.
- Direct/SMTP/MIME/XDM transport — replaced by a JSON envelope; add a HISP front end.
- C-CDA parsing is pragmatic (one resource per coded entry); implement the full
  C-CDA on FHIR mapping and validate against US Core + ODE profiles.
- Correlation store is in-memory; back it with a persistent store.
- Directory is a static map; back it with a real provider directory.
- `AuditEvent` is noted but not yet emitted (Provenance is).

---

## Production hardening checklist

- Persistent, restart-safe correlation store; idempotency on redelivered Direct msgs.
- Full Direct/XDM receiver and sender (MDN handling, XDM ZIP, metadata).
- Complete C-CDA on FHIR transform with profile validation (HL7 validator / HAPI).
- ODE dental profiles for tooth numbering, periodontal observations, CDT procedures.
- `Provenance` + `AuditEvent` on every boundary crossing.
- Out-of-order / delayed-decline reconciliation against open and recently-closed episodes.
- Security: Direct trust anchors; SMART/OAuth2 on the FHIR face; PHI handling.

## Conformance (draft)

A conformant 360/ODE Adapter SHALL: implement the 360X Referral Initiator/Recipient
actors on its 360X face and the ODE Native Placer/Fulfiller actors on its FHIR face;
map every supported 360X transaction to the corresponding `Task` transition; transform
content via C-CDA on FHIR and the published C-CDA ↔ US Core mappings; apply ODE dental
profiles and conform to the published loss profile (recording untranslatable content as
narrative/attachment, never dropping it); maintain episode correlation and tolerate
delayed/out-of-order messages; and record `Provenance`/`AuditEvent` across the boundary.

## Pinned dependencies (confirm at pin time)

| Dependency | Target |
|---|---|
| Clinical Order Workflows (COW) | balloted version (O&O, co-sponsor) |
| C-CDA on FHIR | v2.0.0 line (for C-CDA ↔ US Core mappings) |
| US Core | 6.1 |
| IHE 360X | Rev. 1.2 (2021-04-14) + US national extension |

## Module map

```
ode_adapter/
  config.py         versions, terminology systems, LOINC codes, plugin selection
  ports.py          FhirBackend / IheCodec / IheOutboundTransport interfaces
  registry.py       plugin registry (name -> class; entry points)
  xdm.py            Layer 1 transport: XDM envelope model (in/out)
  hl7v2.py          Layer 1 transport: minimal v2 parse/build
  state_machine.py  Layer 2: 360X transaction <-> Task state
  ccda_to_fhir.py   Layer 3 inbound:  C-CDA Referral Note -> FHIR Bundle
  fhir_to_ccda.py   Layer 3 outbound: FHIR result -> C-CDA + loss profile
  stores.py         correlation store + directory
  engine.py         orchestration (depends only on ports)
  app.py            FastAPI: the two faces + /plugins
  plugins/
    fhir/generic_r4.py   any conformant R4 server (default)
    fhir/onyx.py         OnyxOS (server-specific loading)
    fhir/medplum.py      Medplum 5.1.x (OAuth2 backend services + strict search)
    ihe/json_envelope.py default codec + capture transport
    ihe/xdm_zip.py       real XDM ZIP codec (scaffold)
    ihe/direct_smtp.py   Direct/SMTP send (scaffold)
samples/
  referral_request.xml   sample C-CDA Referral Note (dental clearance)
  demo.py                runnable end-to-end demo (no server needed)
  inbound_pcc55.json     generated by demo; use with curl
```

See **ARCHITECTURE.md** for the ports/plugins design and the full repo structure.

## Test data

Synthetic patient data throughout. Per OHIA test-data rules, real NPIs may be paired
with synthetic patient data; never use real patient data in this reference.
