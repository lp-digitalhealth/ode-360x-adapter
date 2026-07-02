# Ode.Adapter (.NET)

A faithful C# port of the Python reference 360X⟷ODE adapter. Same ports-and-adapters
design, same mapping behavior, dependency-free (`System.Text.Json` + `System.Xml.Linq`).

## Layout
```
Ode.Adapter.sln
src/Ode.Adapter/            class library (ports, core, plugins)
  Ports/                    IFhirBackend, IIheCodec, IIheOutboundTransport, envelopes
  Config, Registry, Hl7v2, StateMachine, Stores, JsonX
  Cow, ReferralFhir         COW helpers + rich referral bundle builder
  CcdaToFhir, FhirToCcda, Engine
  Plugins/Fhir/             generic-r4, onyx
  Plugins/Ihe/              json-envelope, capture, http, xdm-zip*, direct*  (*scaffold)
src/Ode.Adapter.Host/       ASP.NET minimal API (mirrors app.py)
samples/Ode.Adapter.Demo/   console demo (mirrors demo.py)
tests/Ode.Adapter.Tests/    dependency-free console test runner (98 checks)
```

## Build, run, test (requires .NET 8 SDK)
```bash
cd dotnet
dotnet build                                   # compile everything

dotnet run --project samples/Ode.Adapter.Demo  # inbound PCC-55 + outbound PCC-57 (dry-run)
dotnet run --project tests/Ode.Adapter.Tests   # PASS/FAIL; exits non-zero on failure
dotnet run --project src/Ode.Adapter.Host      # http://localhost:5000
```

Host endpoints: `POST /360x/inbound`, `POST /ode/task-event`, `POST /ode/referral`
(dental-initiated PCC-55), `POST /ode/appointment-event` (PCC-60/61), `GET /plugins`,
`GET /episodes`, `GET /episodes/{referralId}/inbox`, `GET /healthz`. Runs in dry-run by
default (no FHIR server needed); set `ODE_ADAPTER_DRY_RUN=false` and
`ODE_ADAPTER_ODE_BASE_URL` to drive a real server, and `ODE_ADAPTER_FHIR_BACKEND=onyx`
to select the OnyxOS backend.

See `STATUS.md` for parity details and what is intentionally not yet wired.
