# ODE Reference Implementations

Reference implementations of the **360/ODE Adapter** — a pluggable edge server that
bridges **IHE 360X** (closed-loop referral over Direct/XDM/HL7v2/C-CDA) and **ODE
Native** (FHIR R4 / Clinical Order Workflows, with oral-health profiles).

This repository accompanies the HL7 FHIR **Oral Health Data Exchange (ODE)** IG. The
IG is the normative specification; this repo is the proof it works, runnable in a
container. *(Add the published IG URL here once available.)*

## Layout

| Path | What |
|---|---|
| `spec/` | **the language-neutral contract** — ports, mappings, conformance, use cases (source of truth) |
| `python/` | reference implementation (most complete) |
| `java/` | Java implementation (building to parity) |
| `dotnet/` | .NET implementation (building to parity) |
| `docker/` | `docker compose up` — FHIR server + adapter (Connectathon-in-a-box) |
| `wiki/` | navigation / front door for implementers |
| `PROGRAM-PLAN.md` | the roadmap and the current 3-week sprint |

## Quick start

```bash
# Python reference, zero dependencies:
python python/samples/demo.py

# Everything in containers (FHIR server + adapter):
docker compose -f docker/docker-compose.yml up --build
```

## Principle

One contract, three languages. Every implementation satisfies `spec/`; if behavior
differs, `spec/` is right and the code is wrong. See `CONTRIBUTING.md`.

## License

Apache-2.0 (confirm). See `LICENSE`.
