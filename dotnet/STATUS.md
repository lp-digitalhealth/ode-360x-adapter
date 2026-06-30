# .NET implementation — status

**Reference-complete core, parity with the Python happy path.** Faithful port of the
tested Python reference (`python/ode_adapter/`). Dependency-free: `System.Text.Json`
+ `System.Xml.Linq` only — no FHIR SDK, mirroring the Python approach (FHIR resources
are `JsonObject`s).

> Built offline without a .NET SDK in the authoring environment, so it is **untested
> here** — compile and run locally (commands in README.md). It mirrors the Python
> logic line-for-line where practical, and the test runner reproduces a meaningful
> subset of the 45 Python tests.

## In parity
- Ports: `IFhirBackend`, `IIheCodec`, `IIheOutboundTransport` (+ envelope types).
- Inbound PCC-55 → FHIR transaction Bundle (Patient, Practitioner/Organization,
  Condition, MedicationRequest, AllergyIntolerance, ServiceRequest, Task=requested,
  Provenance). PCC-58 cancellation → Task revoke.
- Outbound state machine: accepted/in-progress→PCC-56(IP); rejected/failed→PCC-56(CA);
  interim→PCC-59; completed→PCC-57(doc); cancelled→PCC-58.
- Loss profile (CDT/tooth/perio → narrative + loss notes) in `FhirToCcda`.
- Plugins: `generic-r4`, `onyx`, `json-envelope`, `capture`, `http` (+ `xdm-zip`,
  `direct` scaffolds). Config-driven selection via `Adapter.FromConfig()`.
- Console demo, console test runner, ASP.NET minimal-API host (mirrors `app.py`).

## Not yet wired (same as Python)
- Real XDM/Direct transport (`xdm-zip` / `direct` throw NotImplemented).
- PCC-60 / PCC-61 appointment + no-show emission paths.
- HTTP/onyx-upsert paths are written but offline-untestable (exercise against a live
  HAPI / OnyxOS server).
