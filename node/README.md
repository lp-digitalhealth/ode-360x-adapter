# Symmetric 360X Edge Server — Mirror Reply Simulators (Node harness UI)

A browser harness that exercises **every 360X ⟷ COW transaction through the live
bridge**, in **both directions**, via two mirror personas over one shared payload
library ([`lib/payloads.js`](lib/payloads.js)):

- **Dental PMS** — the PMS (FHIR) **initiates** a referral; you **simulate the 360X
  replies** the medical peer sends. Replies flow through the bridge, are written to
  FHIR, and the PMS reads them back in its **Inbox**.
- **Medical EHR** — the EHR (360X) **initiates**; you **simulate the dental FHIR
  replies**; the bridge emits the **360X the EHR would ingest**.

Switch personas with the toggle. Every built/converted payload is **exportable** as
files (HL7 v2, C-CDA, 360X envelope, FHIR body, or a combined manifest) for offline
testing with real medical systems.

> **Zero dependencies.** Pure Node standard library — no `npm install`. The Node
> layer never re-implements mapping logic; it only builds each transaction's wire
> payload and forwards it to the bridge's HTTP API.

See the normative mapping in
[`spec/mapping/360x-cow-crosswalk.md`](../spec/mapping/360x-cow-crosswalk.md).

## Architecture

```
browser (persona toggle · initiate · reply simulators · inbox · export)
      |  fetch /api/*
      v
node/server.js                 <- static UI + thin proxy + export (lib/payloads.js)
      |  builds 360X envelope / FHIR body
      v
Python bridge HTTP API         <- the real implementation
   POST /ode/referral             (dental initiate:  FHIR -> 360X PCC-55)
   POST /360x/inbound             (inbound: 360X -> FHIR; incl. reply ingestion)
   POST /ode/task-event           (outbound: FHIR -> 360X status/outcome)
   POST /ode/appointment-event    (outbound: FHIR -> 360X SIU PCC-60/61)
   GET  /episodes/:id/inbox       (the FHIR the PMS/EHR reads back)
   GET  /episodes /plugins /healthz
```

## Run it

**Option A — everything in Docker (live FHIR loop):**

```bash
docker compose -f docker/docker-compose.yml up --build
# UI:   http://localhost:4000     bridge: :8000     HAPI FHIR: :8080/fhir
```

This runs the bridge with `DRY_RUN=false`, so conversions are **written to HAPI** and
the Inbox reads them **back from the server**.

**Option B — run the pieces directly (dry-run, no FHIR server needed):**

```bash
# 1) bridge
cd python && pip install -e ".[server,fhir]" && uvicorn ode_adapter.app:app --port 8000
# 2) UI (another terminal)
cd node && npm start        # or: node server.js
```

**Windows (PowerShell)** — run each command on its own line (PowerShell has no `&&`
chaining) and invoke uvicorn as a module (the `uvicorn` script is often not on
`PATH`):

```powershell
# 1) bridge  (first run only: pip install -e ".[server,fhir]")
cd python
python -m uvicorn ode_adapter.app:app --port 8000

# 2) UI  (another terminal)
cd node
node server.js
```

Open <http://localhost:4000>. In dry-run the Inbox reads the bridge's per-episode
cache (what it would have written); flip `ODE_ADAPTER_DRY_RUN=false` + point
`ODE_ADAPTER_ODE_BASE_URL` at a FHIR server to read live.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `PORT` | `4000` | port for this UI server |
| `ADAPTER_URL` | `http://127.0.0.1:8000` | base URL of the running bridge |

## Transactions (both personas)

| 360X | HL7 v2 | Dental persona (PMS initiates) | Medical persona (EHR initiates) |
|---|---|---|---|
| PCC-55 | `OMG^O19` | **Initiate** → bridge emits PCC-55 | **Initiate** → bridge writes FHIR Task |
| PCC-56 accept | `OSU^O51` (IP) | simulate reply → Task accepted | simulate dental accept → emit OSU (IP) |
| PCC-56 decline | `OSU^O51` (CA) | simulate reply → Task rejected, Request revoked | simulate dental decline → emit OSU (CA) |
| PCC-59 interim | `OMG^O19` + doc | simulate reply → interim `Task.output` | simulate dental interim → emit note |
| PCC-57 outcome | `OMG^O19` + doc | simulate reply → Task+Request completed | simulate dental outcome → emit note (**loss profile**) |
| PCC-60 appointment | `SIU^S12` | simulate reply → `Appointment` (booked) | simulate dental booking → emit SIU |
| PCC-61 no-show | `SIU^S26` | simulate reply → `Communication` | simulate dental no-show → emit SIU |

Each reply carries the COW `businessStatus` from the crosswalk
(`referral-sent` → `accepted`/`declined` → `interim-results` → `outcome-final`,
plus `appointment-booked`/`appointment-noshow`).

## Export

Use the **Export payload** chips on any run, or hit the endpoint directly:

```
GET /api/export?persona=dental&action=pcc57-outcome&referralId=REF-1001&part=all
#   part = all | envelope | hl7 | cda | fhir
```

Returns a downloadable file so you can inspect or replay the payload offline.

## Notes

- The sample Referral Note C-CDA is read from
  `../python/samples/referral_request.xml` so the harness and the Python reference
  never drift.
- Both personas share `lib/payloads.js`, so **what you export is exactly what gets
  pushed live** through the bridge.
