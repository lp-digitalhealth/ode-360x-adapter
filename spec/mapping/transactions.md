# 360X transaction ⟷ FHIR Task state (+ v2 bindings)

Orientation: the adapter sits on the **dental edge**. Medical EHR = Referral
Initiator; dental (ODE Native) = Referral Recipient / Fulfiller.

| 360X transaction | v2 message | Direction | Task interaction | Task.status | Reply content |
|---|---|---|---|---|---|
| Referral Request `PCC-55` | `OMG^O19` | inbound | create Task + ServiceRequest | requested | referral package |
| Request Status `PCC-56` (accept) | `OSU^O51` | outbound | Fulfiller accepts | accepted/in-progress | `Task.owner` (accepting provider), `Task.note`, `Task.restriction.period.end` |
| Request Status `PCC-56` (decline) | `OSU^O51` | outbound | Fulfiller rejects | rejected | `Task.statusReason` (coded), `Task.note` |
| Interim Consultation Note `PCC-59` | `OMG^O19`+doc | outbound | interim result | in-progress | interim `Observation` + note (Task.output) |
| Referral Outcome `PCC-57` | `OMG^O19`+doc | outbound | final result, close | completed | clearance/disposition `Observation` + outcome resources |
| Referral Cancellation `PCC-58` | `OSU^O51` | inbound | revoke | cancelled | — |
| Appointment Notification `PCC-60` | `SIU^S12` | outbound | Appointment event | (unchanged) | `start`/`end`, provider, `appointmentType`, location |
| No-Show Notification `PCC-61` | `SIU^S26` | outbound | notification | (unchanged) | `Communication.reasonCode` + reschedule |

v2 bindings confirmed against IHE PCC 360X Rev. 1.2 (2021-04-14). Field-level
bindings must be validated against 360X Volume 2.

The full bidirectional mapping (360X ⟷ COW/FHIR, including `Task.businessStatus`,
`Task.output`, and the `Request.status` lifecycle) is the keystone artifact in
[360x-cow-crosswalk.md](360x-cow-crosswalk.md). **COW is scoped there to only what
360X supports for this use case.** This table is the workflow-state summary; the
crosswalk is normative.
