# UC01 — Head & Neck Cancer: dental clearance before radiation

**Pattern:** Medical provider → dentist (Pattern B; 360/ODE Adapter).
**Story:** Oncology refers a head & neck cancer patient for dental clearance prior to
radiation. The dentist evaluates, remediates active disease, and returns clearance.

## Flow
1. Medical EHR sends **PCC-55 Referral Request** (C-CDA Referral Note: problems
   incl. malignant tumor of tongue, medications, allergies, reason for referral).
2. Adapter → FHIR Bundle (ServiceRequest + Task=requested + supporting resources) on
   ODE Native.
3. Dentist accepts (**PCC-56**), optionally interim note (**PCC-59**).
4. Dentist completes with clearance + CDT procedures (e.g., D7140 extraction) and
   perio findings → **PCC-57 Referral Outcome** (Consultation Note). Dental specifics
   carried as narrative per the loss profile.

## Conformance expectations
- Inbound Bundle contains Patient, Practitioner, Organization, Condition
  (363349007 malignant tumor of tongue), MedicationRequest, AllergyIntolerance,
  ServiceRequest, Task, Provenance.
- Outbound Consultation Note includes Assessment & Plan + a flagged Dental Findings
  narrative section + loss notes for CDT/perio.

## Fixtures
- `python/samples/referral_request.xml` (canonical C-CDA — to be centralized in
  `spec/fixtures/` in Phase 2)
- `python/samples/inbound_pcc55.json` (envelope)

## Status by language
| Python | Java | .NET |
|---|---|---|
| ✅ inbound + outbound (happy path) | ☐ | ☐ |
