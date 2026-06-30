# UC03 — Pediatric Referral: periodontitis via HIE (Connie)

**Pattern:** HIE → dentist (Pattern C), with periodontal dental content.
**Story:** A pediatric patient (Husky B / CHIP) with periodontitis is referred to a
dentist; the referral is routed through an HIE (Connie), which speaks 360X or ODE
Native. Epic Care Everywhere on the source side.

## Flow
1. HIE delivers a referral for a pediatric patient with a periodontal condition.
   - Path A: 360X (Direct/C-CDA) → adapter → ODE Native.
   - Path B: ODE Native (FHIR) → directly to the dental endpoint.
2. Dental side creates Task + ServiceRequest; pediatric demographics preserved.
3. Dentist returns periodontal findings — structured on ODE Native; narrative + loss
   notes on the 360X path.

## Conformance expectations
- Pediatric Patient (birthDate within childhood; correct gender/identifiers).
- Periodontal Observation(s) (pocket depth, etc.) represented on ODE Native; loss
  profile applied if returned over 360X.
- Condition coded for periodontitis (SNOMED/SNODENT).

## Fixtures (to add — Week 2)
- `spec/fixtures/uc03_pediatric_referral.xml` (C-CDA) and/or a FHIR bundle for Path B
- Synthetic pediatric patient; real NPIs per OHIA test-data rules.

## Status by language
| Python | Java | .NET |
|---|---|---|
| ☐ (Week 2 target) | ☐ | ☐ |
