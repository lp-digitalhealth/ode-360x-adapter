# 360X transaction ⟷ FHIR Task state (+ v2 bindings)

Orientation: the adapter sits on the **dental edge**. Medical EHR = Referral
Initiator; dental (ODE Native) = Referral Recipient / Fulfiller.

| 360X transaction | v2 message | Direction | Task interaction | Task.status |
|---|---|---|---|---|
| Referral Request `PCC-55` | `OMG^O19` | inbound | create Task + ServiceRequest | requested |
| Request Status `PCC-56` (accept) | `OSU^O51` | outbound | Fulfiller accepts | accepted/in-progress |
| Request Status `PCC-56` (decline) | `OSU^O51` | outbound | Fulfiller rejects | rejected |
| Interim Consultation Note `PCC-59` | `OMG^O19`+doc | outbound | interim result | in-progress |
| Referral Outcome `PCC-57` | `OMG^O19`+doc | outbound | final result, close | completed |
| Referral Cancellation `PCC-58` | `OSU^O51` | inbound | revoke | cancelled |
| Appointment Notification `PCC-60` | `SIU^S12` | outbound | Appointment event | (unchanged) |
| No-Show Notification `PCC-61` | `SIU^S26` | outbound | notification | (unchanged) |

v2 bindings confirmed against IHE PCC 360X Rev. 1.2 (2021-04-14). Field-level
bindings must be validated against 360X Volume 2.
