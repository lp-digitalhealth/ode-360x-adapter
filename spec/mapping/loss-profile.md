# Dental loss profile

Inbound medical content bridges cleanly. **Outbound dental content has no structured
C-CDA slot** and must be rendered as flagged narrative with an emitted loss note —
never dropped. This asymmetry is the argument for ODE Native end-to-end.

| Dental data | ODE Native | On the 360X bridge |
|---|---|---|
| CDT procedures (`http://www.ada.org/cdt`) | Procedure.code | narrative only |
| Tooth numbering (Universal / FDI ISO 3950) | bodySite (ODE ext.) | narrative only |
| Periodontal observations | ODE perio Observation | narrative only |
| Dental diagnoses (SNODENT) | Condition.code | narrative only |
| Radiographs / images | DocumentReference / Media | attachment, metadata lost |

Conformance: an implementation MUST record untranslatable dental content as
narrative/attachment and emit a loss note; dropping it is non-conformant.
