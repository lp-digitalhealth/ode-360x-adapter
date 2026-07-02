# 360X ⟷ COW crosswalk (the keystone mapping)

This is the **single source of truth** for how the edge server translates between
**IHE 360X** (HL7 v2 + C-CDA over Direct/XDM) and **HL7 Clinical Order Workflows
(COW)** on FHIR R4. The bridge code in every language implements *this table*; the
provisional dental COW profile and the FHIR state alignment derive from it.

## Scope rule (read this first)

**COW is scoped to ONLY what 360X supports for this use case.** This crosswalk does
not adopt all of COW — it adopts exactly the subset that the seven 360X referral
transactions (PCC-55…61) exercise. COW capabilities that 360X does not carry for a
1:1 closed-loop referral are **intentionally out of scope** (listed at the bottom)
so that round-tripping 360X ⟷ COW ⟷ 360X is lossless on the workflow axis.

The server is **symmetric**: each transaction has a *360X→COW* direction (a 360X
message arrives, the bridge writes COW/FHIR) and a *COW→360X* direction (a FHIR
state change occurs, the bridge emits a 360X message). Which direction is "inbound"
depends on who initiated:

- **Dental-initiated:** dental PMS (FHIR) places the referral; the medical peer (360X)
  replies. The bridge does COW→360X for the request/cancel and 360X→COW for the replies.
- **Medical-initiated:** medical EHR (360X) places the referral; the dental side (FHIR)
  replies. The mirror of the above.

## 1. Transaction crosswalk

| 360X transaction | v2 message | COW / FHIR representation | Request.status | Task.status | Task.businessStatus | Task.output |
|---|---|---|---|---|---|---|
| **PCC-55** Referral Request | `OMG^O19` | create directional `ServiceRequest` (ODE profile per direction — see §4) + `ODEReferralTask` (`code=fulfill`), `Task.input` = referral package (DocumentReference + supporting clinical resources: `Condition`, `MedicationRequest`, `ODEMedicationList`, `AllergyIntolerance`), `ServiceRequest.supportingInfo` → the `ODEMedicationList` (+ allergy) | `active` | `requested` | `received` | — |
| **PCC-56** Status — accept | `OSU^O51` (IP) | update `Task` (+ `Task.owner`, `Task.note`, `Task.restriction.period.end`) | `active` | `accepted` / `in-progress` | `accepted` | — |
| **PCC-56** Status — decline | `OSU^O51` (CA) | update `Task` (+ `Task.statusReason`, `Task.note`) | `revoked` | `rejected` | `declined` | — |
| **PCC-59** Interim Consultation Note | `OMG^O19` + doc | update `Task` + interim Observation/Procedure as `Task.output` | `active` | `in-progress` | `interim-results` | interim resources incl. an interim `Observation` |
| **PCC-57** Referral Outcome | `OMG^O19` + doc | update `Task`, attach outcome resources, close the Request | `completed` | `completed` | `outcome-final` | ClinicalImpression, Procedure, CarePlan, Observation (incl. a coded clearance/disposition `Observation`), DocumentReference |
| **PCC-58** Referral Cancellation | `OSU^O51` (CA) | revoke the Request + Task | `revoked` | `cancelled` | `cancelled` | — |
| **PCC-60** Appointment Notification | `SIU^S12` | `Appointment` (booked) linked to the Task — `start`/`end`, participant practitioner, `appointmentType`, `description`=location | `active` | `in-progress` | `appointment-booked` | Appointment |
| **PCC-61** No-Show Notification | `SIU^S26` | `Communication` (no-show) linked to the Task — `reasonCode` + reschedule `payload` | `active` | `in-progress` | `appointment-noshow` | Communication |

Notes:
- The **referral id** is the loop key. On the v2 side it travels in `ORC-2` (placer
  order number); on the FHIR side it is `ServiceRequest.identifier` and
  `Task.identifier` with system `urn:ohia:referral-id`.
- `businessStatus` codes above are the **ODE referral sub-status** value set
  (`http://ohia-codes.org/CodeSystem/ode-referral-sub-status`) — the CodeSystem the ODE
  Native contract ([../api/ode-openapi.yaml](../api/ode-openapi.yaml)) uses. `received`
  and `scheduled` are the contract's example codes; the remaining codes are the
  additional 360X-driven states this bridge tracks. (PCC-55 intake stamps `received`.)

### 1a. Reply content payloads (not just a status flip)

Each reply carries a real content payload. It is **lossless on the FHIR (COW) side**
(what the initiator reads from its inbox) and **degraded on the 360X v2 side** (free
text / a single provider field where v2 has no structured home):

| Reply | Content (FHIR — lossless) | 360X v2 degrade |
|---|---|---|
| **PCC-56 accept** | `Task.owner` = accepting provider (`PractitionerRole`+`Practitioner`+`Organization`), `Task.note` = acknowledgment, `Task.restriction.period.end` = expected timeframe | `ORC-12` accepting provider, `NTE` note, `ORC-15` expected-by date |
| **PCC-56 decline** | `Task.statusReason` = coded reason (`urn:ohia:cow:decline-reason`), `Task.note` = comment, `ServiceRequest` → `revoked` | `ORC-16` reason code, `NTE` comment |
| **PCC-59 interim** | interim `Observation` (finding/value) + `ClinicalImpression`/`CarePlan`/`DocumentReference` as `Task.output` | `OMG^O19` + C-CDA Consultation Note (`NTE` optional) |
| **PCC-57 outcome** | coded clearance/disposition `Observation` + outcome resources; close Task + Request | `OMG^O19` + C-CDA Consultation Note (disposition in narrative) |
| **PCC-60 appointment** | `Appointment.start`/`end`, participant practitioner, `appointmentType`, `description`=location | `SCH-11`/`SCH-12` start/end, `ORC-12` provider, `NTE` location/type |
| **PCC-61 no-show** | `Communication.reasonCode` + reschedule `payload` | `SIU^S26`, `NTE` reason/reschedule |

## 2. Segment / element crosswalk

| 360X element | FHIR element |
|---|---|
| `MSH-9` message type | (selects the transaction; not persisted) |
| `ORC-1` order control (`NW`/`SC`) | implied by transaction |
| `ORC-2` placer order number | `ServiceRequest.identifier` / `Task.identifier` (`urn:ohia:referral-id`) |
| `ORC-5` order status (`IP`/`CA`/`CM`) | drives `Task.status` (see table 1) |
| `ORC-12` ordering/accepting provider | `Task.owner` (accepting provider on PCC-56 accept) |
| `ORC-15` order effective date | `Task.restriction.period.end` (expected timeframe on accept) |
| `ORC-16` order control reason | `Task.statusReason` (coded decline reason on PCC-56 decline) |
| `NTE` notes/comment | `Task.note` (accept/decline) / `Communication` payload (no-show) |
| `SCH-11` / `SCH-12` (SIU) | `Appointment.start` / `Appointment.end` |
| `SCH` / `AIL` (SIU) | `Appointment.status` |
| C-CDA Medications section (`10160-0`) | `MedicationRequest` (RxNorm) aggregated into an `ODEMedicationList` (`List`, LOINC `10160-0`), referenced by `ServiceRequest.supportingInfo` |
| C-CDA document (per `content.md`) | decomposed resources (see `content.md`) |

C-CDA section ⟷ resource decomposition is defined in
[content.md](content.md); the loss profile (dental content with no structured C-CDA
home) is defined in [loss-profile.md](loss-profile.md). The workflow-state mapping is
summarized in [transactions.md](transactions.md).

## 3. COW subset adopted (in scope)

- `Task` as the Coordination Task (`code=fulfill`, `intent=order`, `focus`→Request,
  `for`→Patient).
- `Task.status` values: `requested`, `accepted`, `in-progress`, `completed`,
  `rejected`, `cancelled`.
- `Task.businessStatus` from the ODE referral sub-status value set above (granular
  360X-driven progress).
- `Task.input` (the referral package) and `Task.output` (results/outcome resources).
- **Referral supporting clinical resources** — the referral package carries, and the
  order references, the clinical context needed to diagnose/treat/bill: `Condition`
  (diagnoses, via `ServiceRequest.reasonReference`), the current medication list as an
  **`ODEMedicationList` (`List`, LOINC `10160-0`) whose entries reference US Core
  `MedicationRequest` (RxNorm)**, and `AllergyIntolerance` (both via
  `ServiceRequest.supportingInfo`). These decompose from / recompose to the C-CDA
  Problems (`11450-4`), Medications (`10160-0`) and Allergies (`48765-2`) sections (see
  [content.md](content.md)).
- `Task.owner` — **scoped to the accepting-provider identity only** (who accepted the
  referral on PCC-56 accept), not full COW baton-passing / reassignment.
- `Task.statusReason` — coded reason on decline (`urn:ohia:cow:decline-reason`).
- `Task.note` — acknowledgment / decline comment / free-text reply notes.
- `Task.restriction.period.end` — the expected timeframe an accept commits to.
- `Request.status` (`ServiceRequest`) lifecycle: `active` → `completed` / `revoked`,
  kept consistent with `Task.status` (authorization vs execution separation).

## 4. ODE directional referral profiles (what native ODE servers expect)

The referral order carries a directional ODE profile on `ServiceRequest.meta.profile`,
one per direction that involves dental (mirrors the ODE Native contract in
[../api/ode-openapi.yaml](../api/ode-openapi.yaml)). The bridge stamps the profile and
enforces the direction's coding must-support — the rule is: **code each referral for the
world the *receiving clinician* acts and bills in.**

| Direction | `ServiceRequest.meta.profile` | Must-support coding |
|---|---|---|
| **medical → dental** | `…/ode-medical-to-dental-referral` | ICD-10-CM `reasonCode` + CPT/HCPCS `code`; **no CDT** (tooth *should*) |
| **dental → dental** | `…/ode-dental-to-dental-referral` | CDT `code` + tooth `bodySite`; SNODENT *should*; medical codes not required |
| **dental → medical** | `…/ode-dental-to-medical-referral` | ICD-10-CM `reasonCode` + CPT/HCPCS `code`; **no CDT**; SNODENT *should*; screening result in `supportingInfo` |

Canonical profile base: `https://oralhealthalliance.net/fhir/StructureDefinition/…`.
The workflow object is `ODEReferralTask` (`…/ode-referral-task`, base FHIR `Task`) and the
medication list is `ODEMedicationList` (`…/ode-medication-list`, base FHIR `List`). For
medical-side receivers (medical→dental, dental→medical) the bridge drops any CDT coding
from `ServiceRequest.code` while keeping its text, so the requested service stays legible
without carrying a code system the receiver can't bill.

## 5. COW capabilities intentionally OUT of scope (not exercised by 360X here)

These are valid COW features the crosswalk deliberately omits because the 360X 1:1
closed-loop referral does not carry them; the server may add them later without
breaking this crosswalk:

- **Cancellation Request Task** — 360X cancellation is a single `OSU^O51 (CA)`
  message, mapped directly to revoke; no separate request-to-cancel Task.
- **`Task.performer` baton / reassignment** — `Task.owner` is used only to record the
  accepting provider's identity (see in-scope); 360X has two fixed actors, so there is
  no fulfiller hand-off or ownership transfer.
- **Multiple Coordination Tasks per Request / bidding** (`code=request-fulfillment`,
  fulfiller selection) — 360X referrals are point-to-point.
- **FHIR Subscriptions / `watch_tasks`** — the bridge is message-driven; polling/notify
  is a deployment concern, not part of the crosswalk.
- **`Task.partOf` sub-tasks** — no sub-task decomposition in 360X.

> Rule: if a future dental use case needs one of these, add it to this crosswalk first,
> then to the code — never the other way around.
