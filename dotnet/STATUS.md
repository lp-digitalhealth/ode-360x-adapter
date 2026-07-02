# .NET implementation — status

**Reference-complete core, full parity with the Python reference.** Faithful port of
the tested Python reference (`python/ode_adapter/`). Dependency-free: `System.Text.Json`
+ `System.Xml.Linq` only — no FHIR SDK, mirroring the Python approach (FHIR resources
are `JsonObject`s).

> Verified locally with the .NET SDK: `dotnet build` succeeds and the console test
> runner reports **98 PASS / 0 FAIL** (a superset covering inbound/outbound, reply
> ingestion, appointment/no-show, medication list, and ODE-contract conformance).

## In parity
- Ports: `IFhirBackend` (extended `UpdateTaskStatus` + `UpdateRequestStatus` +
  `FindByReferral`), `IIheCodec`, `IIheOutboundTransport` (+ envelope types).
- Inbound PCC-55 → FHIR transaction Bundle: Patient (+address/phone), referring
  provider (author) **and** rendering provider (informationRecipient) as
  Practitioner/PractitionerRole/Organization, Condition, MedicationRequest,
  AllergyIntolerance, Coverage (payers), an **ODEMedicationList** (`List`), a
  directional ServiceRequest, Task (`ode-referral-task`, businessStatus `received`),
  Provenance. PCC-58 cancellation → Task revoke.
- Reply ingestion (`InboundReply`): PCC-56 accept/decline (owner/statusReason/note/
  period), PCC-57 outcome, PCC-59 interim, PCC-60 appointment, PCC-61 no-show — with
  the COW Task/Request state change, `Task.output`, and the harness inbox cache.
- Dental-initiated PCC-55 (`HandleReferralInitiation`) via `ReferralFhir` + rich
  Referral Note C-CDA, and outbound `HandleAppointmentEvent` (PCC-60/61).
- Outbound state machine: accepted/in-progress→PCC-56(IP); rejected/failed→PCC-56(CA);
  interim→PCC-59; completed→PCC-57(doc); cancelled→PCC-58. Reply content rides the
  degraded v2 (ORC-12/15/16, NTE, SCH-11/12).
- ODE-contract conformance: directional `ServiceRequest.meta.profile`, must-support
  CDT drop for medical-side receivers, `ohia-codes.org` businessStatus system,
  `referral-id` token search (`FindByReferral`).
- Loss profile (CDT/tooth/perio → narrative + loss notes) in `FhirToCcda`.
- Plugins: `generic-r4`, `onyx`, `json-envelope`, `capture`, `http` (+ `xdm-zip`,
  `direct` scaffolds). Config-driven selection via `Adapter.FromConfig()`.
- Host endpoints: `/360x/inbound`, `/ode/task-event`, `/ode/referral`,
  `/ode/appointment-event`, `/episodes`, `/episodes/{id}/inbox`, `/plugins`, `/healthz`.
- Console demo, console test runner (98 checks), ASP.NET minimal-API host.

## Not yet wired (same as Python)
- Real XDM/Direct transport (`xdm-zip` / `direct` throw NotImplemented).
- HTTP/onyx-upsert and live `FindByReferral` paths are written but exercised only
  against a live HAPI / OnyxOS server (dry-run uses the per-episode inbox cache).

## Notes
- The standalone `src/Ode.Adapter.Ports/` project is not in the solution (dead code);
  the active `IFhirBackend` lives in `src/Ode.Adapter/Ports/Ports.cs`.
