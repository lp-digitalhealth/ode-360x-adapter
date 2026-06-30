# Ports contract

Every implementation exposes the same three ports (interfaces). Concrete plugins
implement them; the mapping core depends only on them. Method names are normative;
language-idiomatic casing is fine (`submit_referral_bundle` / `submitReferralBundle`
/ `SubmitReferralBundle`).

## FhirBackend — drives an ODE Native FHIR R4 server
| Method | Input | Output | Notes |
|---|---|---|---|
| submit_referral_bundle | transaction `Bundle` | transaction-response `Bundle` | POST or server-specific load |
| update_task_status | task id, status, reason? | `Task` | e.g. cancelled on PCC-58 |
| get_task | task id | `Task` | |
| fetch_results (opt) | task id | result resources | for outbound documents |
| watch_tasks (opt) | handler | — | Subscriptions or polling |

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
