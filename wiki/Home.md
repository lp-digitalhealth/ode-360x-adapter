# ODE Reference Implementations — Wiki

The front door for implementers and contributors.

## Navigation
- **Start here**: what this is, and how it relates to the FHIR IG → `Overview`
- **Architecture**: ports & adapters, the three layers → `/spec/` and `python/ARCHITECTURE.md`
- **The contract** (all languages): `/spec/contract/`, `/spec/mapping/`
- **Run it**: `Quick-Start` (Docker) → `docker/docker-compose.yml`
- **Use cases**: `/spec/use-cases/` (UC01 head & neck cancer, UC03 pediatric)
- **By language**: `python/` (reference) · `java/` · `dotnet/`
- **Contribute**: `/CONTRIBUTING.md`, conformance checklist, good-first-issues
- **The IG**: link to the published HL7 FHIR IG (ODE) ← add URL when published

## Status at a glance
| Capability | Python | Java | .NET |
|---|---|---|---|
| Ports/contract | ✅ | ✅ (interfaces) | ✅ (interfaces) |
| PCC-55 inbound | ✅ | ☐ | ☐ |
| PCC-57 outbound | ✅ | ☐ | ☐ |
| PCC-56/58/59/60/61 | ⏳ | ☐ | ☐ |
| Real XDM / Direct | ⏳ | ☐ | ☐ |
| Container | ✅ | ☐ | ☐ |

> This file can be pushed to the GitHub Wiki or rendered via GitHub Pages.
