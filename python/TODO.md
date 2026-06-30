# 360/ODE Adapter — Reference Implementation TODO

Path from the current **v0.1** skeleton (PCC-55 inbound + PCC-57 outbound happy path)
to a **complete, fully-documented reference implementation (v1.0)**.

**Priority:** `P0` required for "all functions implemented + documented reference" ·
`P1` expected for a credible reference · `P2` production-hardening (beyond reference).

### Definition of done (v1.0 reference release)
- All **7 360X transactions** implemented in **both directions**, driven by real ODE Native events.
- Consumes/produces **real XDM packages** (not just the JSON envelope), with a pluggable transport.
- **Full C-CDA ⟷ FHIR** mapping with **profile/schema validation** on both sides.
- **Persistent, idempotent** correlation store with out-of-order tolerance.
- **FHIR Subscriptions** drive the outbound path (no manual `/ode/task-event` poke).
- **Test suite** (unit + integration + the 6 Connectathon scenarios + negatives) green in CI.
- **Complete docs**: API, mapping reference, loss-profile catalog, config, deploy, contribute, conformance.

---

## 1. Workflow transactions — complete all 7, both directions
*(modules: `state_machine.py`, `engine.py`, `hl7v2.py`, `fhir_to_ccda.py`)*

- [ ] `P0` **PCC-56 Referral Request Status (accept)** — emit `OSU^O51` on `Task.status → accepted/in-progress`. Wire `engine.handle_task_event` to a real Subscription event.
- [ ] `P0` **PCC-56 Referral Request Status (decline)** — emit `OSU^O51` on `Task.status → rejected`, including reason mapping (`Task.statusReason`).
- [ ] `P0` **PCC-58 Cancellation (inbound)** — replace the record-only stub in `engine._inbound_cancellation` with a real ODE Native `Task.status=cancelled` PATCH + `ServiceRequest` revoke.
- [ ] `P0` **PCC-59 Interim Consultation Note** — emit on interim result while in-progress; reuse `fhir_to_ccda.build_consultation_note(interim=True)`; define what triggers it (new result resource on open Task).
- [ ] `P0` **PCC-60 Appointment Notification** — build `SIU^S12` from an ODE `Appointment` (date/time, location, provider); add SCH/AIS/AIL/PID segments in `hl7v2.py`.
- [ ] `P0` **PCC-61 No-Show Notification** — build `SIU^S26` from a missed `Appointment`.
- [ ] `P1` **PCC-55 inbound — completeness** — handle multiple documents, multiple problems/meds/allergies, repeat referrals (new referral ID per `360X` rules), and re-referral chaining.
- [ ] `P1` **Transaction acknowledgements** — model the 360X ACK/MDN semantics for each transaction (success/failure receipts), not just the payload.
- [ ] `P1` **Episode lifecycle enforcement** — a small state machine that rejects invalid transitions (e.g., outcome before accept) and records why.

## 2. Transport (Layer 1) — make it real
*(modules: `xdm.py`, `hl7v2.py`, new `transport/`)*

- [ ] `P0` **XDM ZIP pack/unpack** — parse a real IHE XDM package (`METADATA.XML`, `INDEX.HTM`, `IHE_XDM/`, submission set + `DocumentEntry`s) into `InboundEnvelope`; build the ZIP for outbound. (Today the envelope is pre-unpacked JSON.)
- [x] `P0` **Pluggable transport interface** — `IheCodec` + `IheOutboundTransport` ports + registry exist; `json-envelope`/`capture` implemented. XDM/Direct providers remain (below).
- [ ] `P1` **Direct/SMTP/MIME** receiver + sender (HISP integration), with **MDN** (Message Disposition Notification) processed/dispatched.
- [ ] `P1` **Full HL7 v2 messages** — build conformant `OMG^O19` (MSH/PID/PV1/ORC/OBR/…), `OSU^O51`, `SIU^Sxx` per **360X Volume 2**; replace the minimal pipe-builder. Consider a v2 library.
- [ ] `P1` **Validate v2 bindings against 360X Vol. 2** — confirm `TX_MESSAGE_TYPE` and segment/field bindings; remove the "illustrative" caveat once verified.
- [ ] `P2` XDR (point-to-point) transport option in addition to XDM-over-Direct.

## 3. Content transform (Layer 3) — full mapping + validation
*(modules: `ccda_to_fhir.py`, `fhir_to_ccda.py`)*

- [ ] `P0` **Full C-CDA on FHIR inbound mapping** — all relevant sections with multiple entries (Problems, Medications, Allergies, Results, Procedures, Encounters, Vitals, Immunizations, Plan), templateId-aware, beyond one-resource-per-section.
- [ ] `P0` **Structured outbound** — build a proper C-CDA Consultation Note with **structured entries** where the data exists, not just narrative; keep the narrative+loss path only for dental-origin data.
- [ ] `P0` **FHIR profile validation** — validate every generated resource against **US Core 6.1 + ODE profiles** (HL7 validator / HAPI) in tests and optionally at runtime.
- [ ] `P0` **C-CDA validation** — schema + Schematron validation of generated documents.
- [ ] `P0` **Loss-profile catalog** — implement the full dental element list (CDT, tooth Universal/FDI, perio, SNODENT, imaging) with explicit handling + emitted loss notes; make it a documented, testable contract.
- [ ] `P1` **Da Vinci CDex framing** — model the structured provider-to-provider return via CDex where applicable (CDex is a core component per the IG).
- [ ] `P1` **Identity reconciliation** — patient/provider matching between the C-CDA header and FHIR `Patient`/`Practitioner` (id systems, name/DOB fallback), surfaced in the directory.
- [ ] `P1` **Narrative generation** — generate readable `<text>` for every FHIR resource and every C-CDA section.

## 4. State, correlation & reliability
*(module: `ode_client.py` → split into `stores/`)*

- [ ] `P0` **Persistent correlation store** — SQLite reference implementation behind the `CorrelationStore` interface (today it's in-memory).
- [ ] `P0` **Idempotency** — dedupe on Direct message ID / submission set ID; safe redelivery handling.
- [ ] `P0` **Out-of-order / delayed-message reconciliation** — delayed decline after accept, post-completion notifications, late cancellations resolved against open and recently-closed episodes.
- [ ] `P1` **Retry/backoff + dead-letter** for ODE Native calls; surface failures rather than losing messages.
- [ ] `P1` **Episode query/inspection** API improvements (filter by status, referral id, date).

## 5. ODE Native integration
*(module: `ode_client.py`)*

- [ ] `P0` **FHIR Subscriptions (Backport R4)** — subscribe to `Task` changes so the outbound path is event-driven; replace the manual `/ode/task-event` endpoint as the primary trigger.
- [ ] `P1` **Polling fallback** for servers without Subscriptions.
- [ ] `P1` **SMART on FHIR / OAuth2** (backend services) client for the FHIR face; token management.
- [x] **FHIR backend plugin layer** — `FhirBackend` port + registry; `generic-r4` (any R4 / HAPI) and `onyx` (OnyxOS) plugins exist (selectable via config).
- [ ] `P1` **Live-server validation** against HAPI and **Onyx OnyxOS** (the two candidate backends); confirm transaction-bundle + reference-resolution behavior; validate the `onyx` upsert path.
- [ ] `P1` Read/search helpers for `Task`, `ServiceRequest`, and result resources.

## 6. Security & audit

- [ ] `P1` **`AuditEvent`** emission on every boundary crossing (Provenance already emitted).
- [ ] `P2` Direct **trust anchors / certificate** handling for real Direct exchange.
- [ ] `P2` TLS everywhere; OAuth2 on the FHIR face; secrets management.
- [ ] `P2` **PHI handling** — log redaction, encryption at rest for the store, data-retention policy.

## 7. Observability & ops

- [ ] `P1` **Structured logging** with PHI redaction; correlation/episode id on every log line.
- [ ] `P1` **`/readyz`** with dependency checks (FHIR server, store, transport); keep `/healthz` for liveness.
- [ ] `P1` **Config reference + validation** — every setting documented with defaults; fail fast on bad config.
- [ ] `P1` **Dockerfile + docker-compose** — adapter + HAPI FHIR (+ a Direct/SMTP stub) for one-command local run.
- [ ] `P1` **CI** — lint (ruff), type-check (mypy), tests, coverage gate.
- [ ] `P2` Metrics (transactions processed, errors, latency) for a real deployment.

## 8. Testing

- [ ] `P0` **Unit tests** per module: `hl7v2` parse/build, `xdm` pack/unpack, `state_machine`, `ccda_to_fhir`, `fhir_to_ccda`, stores, `engine`.
- [ ] `P0` **Integration test** — full round trip (PCC-55 → ODE Native → PCC-57) against HAPI in Docker.
- [ ] `P0` **Scenario tests** — one per Connectathon use case (pediatric perio, teledentistry ×2, extraction ×2, OSA screening).
- [ ] `P0` **Negative tests** — malformed/missing C-CDA, orphan cancel, out-of-order, duplicate delivery, invalid transition.
- [ ] `P0` **Validation tests** — generated FHIR validates (US Core/ODE); generated C-CDA validates (schema/Schematron).
- [ ] `P1` **Fixtures** — sample C-CDA per document type (Referral Note, Consultation Note) and sample XDM packages.
- [ ] `P1` Coverage target (e.g. ≥85%) enforced in CI.

## 9. Documentation

- [ ] `P0` **API reference** — publish the FastAPI OpenAPI; document each endpoint, payload, and error.
- [ ] `P0` **Mapping reference** (`docs/mapping.md`) — full transaction↔Task table (done), full C-CDA↔FHIR section/entry mapping, and the complete loss-profile catalog.
- [ ] `P0` **Per-transaction flow docs** — sequence diagram + notes for all 7 transactions, both directions.
- [ ] `P0` **Configuration reference** (`docs/config.md`) — every env var, defaults, examples (dry-run, live, Direct).
- [ ] `P0` **Deployment guide** (`docs/deploy.md`) — Docker, FHIR server setup, Direct/HISP setup, production hardening.
- [ ] `P0` **Developer guide** (`docs/extending.md`) — how to add a transaction, extend the content map, implement a transport.
- [ ] `P1` **Conformance statement** — formalize §"Conformance" in README into a statement + a FHIR `CapabilityStatement` for the FHIR face.
- [ ] `P1` **Architecture doc** — embed the layer/sequence diagrams (link to the ODE architecture spec).
- [ ] `P1` **CONTRIBUTING.md**, **LICENSE** (Apache-2.0 suggested), **CHANGELOG.md**, sample-data catalog.
- [ ] `P1` Keep **README** "implemented vs. stubbed" current as functions land (remove stubs as they're completed).

## 10. Packaging & release

- [ ] `P1` **`pyproject.toml`** — proper packaging, pinned deps, console entry point.
- [ ] `P1` **Versioning + tags** — semantic version; `v1.0.0` when the Definition of Done is met.
- [ ] `P1` **Release notes** per tag; cut a **Connectathon build** tag for July.
