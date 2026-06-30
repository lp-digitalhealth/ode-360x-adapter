# C-CDA ⟷ FHIR content mapping

Documents map onto C-CDA on FHIR document profiles, then decompose into US Core +
ODE resources. Reuse the published C-CDA ⟷ US Core mappings.

| Document (transaction) | C-CDA on FHIR profile | Decomposes to |
|---|---|---|
| Referral Request `PCC-55` | Referral Note (57133-1) | ServiceRequest + Condition + Observation + DocumentReference + Patient/Practitioner/Org |
| Interim Note `PCC-59` | Consultation Note (11488-4) | interim Observation/Procedure/narrative |
| Referral Outcome `PCC-57` | Consultation Note (11488-4) | ClinicalImpression + Procedure + CarePlan |

| C-CDA section (LOINC) | FHIR resource |
|---|---|
| Problems (11450-4) | Condition |
| Medications (10160-0) | MedicationRequest |
| Allergies (48765-2) | AllergyIntolerance |
| Results (30954-2) | Observation / DiagnosticReport |
| Procedures (47519-4) | Procedure |
| Reason for referral (42349-1) | ServiceRequest.reasonCode |
| Plan of treatment (18776-5) | CarePlan / ServiceRequest |
