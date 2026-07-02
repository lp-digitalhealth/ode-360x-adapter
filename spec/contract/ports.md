# Ports contract

Every implementation exposes the same three ports (interfaces). Concrete plugins
implement them; the mapping core depends only on them. Method names are normative;
language-idiomatic casing is fine (`submit_referral_bundle` / `submitReferralBundle`
/ `SubmitReferralBundle`).

The edge server is **symmetric** (both 360X→FHIR and FHIR→360X for every
transaction); the normative content mapping is in
[../mapping/360x-cow-crosswalk.md](../mapping/360x-cow-crosswalk.md).

## FhirBackend — drives an ODE Native FHIR R4 server
| Method | Input | Output | Notes |
|---|---|---|---|
| submit_referral_bundle | transaction `Bundle` | transaction-response `Bundle` | `POST /` transaction Bundle (ODE contract). Carries a directional `ServiceRequest` (ODE profile per direction — see crosswalk §4) + `ODEReferralTask`. Expected supporting set: `Condition` (diagnoses, via `reasonReference`), the current medication list as an `ODEMedicationList` (`List`, LOINC `10160-0`) over US Core `MedicationRequest` (RxNorm), and `AllergyIntolerance` — the medication list + allergy linked from `ServiceRequest.supportingInfo` |
| update_task_status | task id, status, reason?, business_status?, outputs? | `Task` | COW: also carries `businessStatus` (system `http://ohia-codes.org/CodeSystem/ode-referral-sub-status`) + `Task.output` |
| update_request_status | request id, status, reason? | `ServiceRequest` | COW: authorization lifecycle (`completed`/`revoked`) |
| get_task | task id | `Task` | |
| fetch_results (opt) | task id | result resources | reads `Task.output`; for outbound documents |
| find_by_referral (opt) | referral id | resources for the episode | harness "inbox" reads the FHIR the bridge wrote |
| watch_tasks (opt) | handler | — | Subscriptions or polling (out of scope per crosswalk) |

## IheCodec — packaging ⟷ envelope
| Method | Input | Output |
|---|---|---|
| unpack | bytes/dict | InboundEnvelope |
| pack | OutboundEnvelope | bytes/dict |

## IheOutboundTransport — send an outbound 360X message
| Method | Input | Output |
|---|---|---|
| send | packaged message | transport result |

## Envelope shapes
**InboundEnvelope**: direct_message_id, submission_set_id, sender_direct_address,
recipient_direct_address, transaction, hl7v2, documents[] (id, mime_type, content).
**OutboundEnvelope**: transaction, sender_direct_address, recipient_direct_address,
hl7v2, documents[].

## Built-in plugin names (all languages should align)
- FhirBackend: `generic-r4`, `onyx`
- IheCodec: `json-envelope`, `xdm-zip`
- IheOutboundTransport: `capture`, `direct`
