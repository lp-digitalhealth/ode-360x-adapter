# Free FHIR servers — backend options for ODE

A reference for OHIA members choosing a FHIR server to run the adapter against. Scope:
**free or freemium servers that are actively maintained.** The column that matters most
for ODE is **quality-measure calculation** (the `$evaluate-measure` operation, driven by
a CQL engine) — it ties to the CQI co-sponsorship. Only a few free servers do it.

> The adapter's `docker-compose` defaults to **HAPI** because it is the most widely used
> server and has measure evaluation built in. Swap in any R4 server via the `generic-r4`
> plugin (or write a plugin for a server-specific quirk, as with `onyx`).

## Comparison

| Server | Website | License / cost | FHIR versions | Quality-measure calc | Maintained |
|---|---|---|---|---|---|
| **HAPI FHIR** | hapifhir.io | Open source (Apache 2.0) | DSTU2–R5, incl. R4B | **Yes** — embedded CQL + Quality Measure engine; `$evaluate-measure` | Active |
| **Blaze** | samply.github.io/blaze | Open source (Apache 2.0) | R4 | **Yes** — fast embedded CQL; `$evaluate-measure` | Active |
| **Firely Server** | fire.ly | Freemium — free Community license (limited) + free sandbox; commercial otherwise | STU3, R4, R5 | No native measure engine | Active |
| **Aidbox** | health-samurai.io | Freemium — free Dev license (no PHI, 5 GB) + free hosted tier; prod from ~$1,900/mo | STU3, R4, R4B, R5, R6 ballot | No native measure engine | Active |
| **Medplum** | medplum.com | Open source (Apache 2.0) + free hosted dev tier | R4 only | No | Active |
| **Microsoft FHIR Server** | github.com/microsoft/fhir-server | Open source (MIT) | R4 (+ R4B/R5 lineage) | No native measure calc | Active |
| **Spark (Incendi)** | github.com/FirelyTeam/spark | Open source (BSD-3) | R4 | No | Active (reference server) |

## Notes per server

**HAPI FHIR** — The default choice: Java, broad version support, large community, thorough
docs. Measure evaluation is now in core (the former `cqf-ruler` operations migrated upstream
into HAPI's Clinical Reasoning module). Common caveat: high-volume deployments need tuning
(indexing, history). Product of Smile Digital Health. Current 8.x requires Java 17+.

**Blaze** — Purpose-built for fast, population-level CQL evaluation; proven at 60+ research
sites; very high performance over large datasets. R4-only and oriented to research/feasibility
querying rather than being a general application backend. Best when measures/CQL are the point.

**Firely Server** (formerly Vonk) — Strong .NET server with excellent native validation and a
clean multi-version story, from the team that helped author FHIR. The free **Community license**
is capped and the license file expires/needs renewal (a frequent gotcha on test servers). Free
public sandbox at server.fire.ly for testing only.

**Aidbox** — Broadest FHIR-version coverage here, plus SQL-on-FHIR, GraphQL, terminology, and
500+ IGs. The free **Development license** forbids PHI and caps storage at 5 GB, so it is
dev/test only unless licensed. Praised for DX, UI, and support. Not a CQL measure engine.

**Medplum** — More than a server: a headless-EHR / developer platform (auth, bots, React
components) with an R4 datastore. Great when you are building an app, not just storing data.
R5 is paused/experimental. Self-hosting wants mature DevOps; a free hosted dev tier exists.

**Microsoft FHIR Server** — Solid, free, self-hosted C# server; the OSS core behind Azure
Health Data Services. No native measures. (Note: the managed *Azure API for FHIR* is legacy —
no new deployments after 2025-04-01; Health Data Services is the successor.)

**Spark (Incendi)** — Lightweight C# reference/test server, BSD-3. Useful as a reference; Firely
Server is its production-grade successor.

## Deliberately excluded

- **LinuxForHealth FHIR (formerly IBM FHIR Server)** — *Has* CQL + Clinical Reasoning
  (`$evaluate-measure` via `fhir-operation-cpg`) and would otherwise qualify on the measure
  column, but it is **not under active development** (R4/R4B only). Excluded under the
  "must be supported" rule — noted here so the omission is intentional, not an oversight.
- **Google Cloud Healthcare API** / **Azure Health Data Services** — Managed/paid (free trial
  credits only), and no native measure calculation. Not "free."
- **AEGIS WildFHIR Community Edition**, **Kodjin** — Appear on HL7's public test-server list as
  R4 options, but neither does measure calculation and both are narrower in scope.

## Bottom line for ODE

- Need quality-measure calculation (CQI angle): **HAPI** (general purpose, the safe default) or
  **Blaze** (fast population-level CQL).
- Just need a plain R4 endpoint to test the adapter: **HAPI** wins on ubiquity — hence the
  `docker-compose` default.

---
*Sources: vendor sites and docs (hapifhir.io, fire.ly, health-samurai.io, medplum.com,
samply.github.io/blaze), the HL7 Clinical Reasoning module, the cqframework projects list, HL7's
public test-server list, and community comparisons. Verify version/pricing/maintenance details
before committing — they change.*
