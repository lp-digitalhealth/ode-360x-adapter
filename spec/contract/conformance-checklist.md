# Conformance checklist (all languages)

A feature is "done" in a language only when every box for it is checked. This is the
shared definition of done; CI runs the same conformance fixtures against each impl.

## Per-transaction (repeat for PCC-55…61)
- [ ] Parses/builds the v2 message per `mapping/transactions.md`
- [ ] Performs the correct Task state transition
- [ ] Inbound: produces a FHIR Bundle that validates against US Core + ODE profiles
- [ ] Outbound: produces a C-CDA that validates (schema + Schematron)
- [ ] Applies the loss profile where dental content is present
- [ ] Records episode correlation; tolerant of late/out-of-order delivery
- [ ] Emits Provenance (and AuditEvent) across the boundary
- [ ] Passes the shared scenario fixture(s) for the transaction

## Per-port
- [ ] Implements all required methods in `contract/ports.md`
- [ ] Registers under the agreed plugin name
- [ ] Selectable by config without code change

## Per use-case (UC01, UC03, …)
- [ ] Runs end-to-end against a live FHIR server in a container
- [ ] Produces the expected resources/documents named in the use-case spec
- [ ] Scenario test green in CI
