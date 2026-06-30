# `spec/` — the language-neutral contract

This directory is the **single source of truth** for every implementation (Python,
Java, .NET). It is not code; it is the contract that all code must satisfy. A
contributor implementing a feature in any language implements it against `spec/`,
not by reading another language's source.

| File | What it defines |
|---|---|
| `contract/ports.md` | the three ports every implementation exposes |
| `contract/conformance-checklist.md` | per-feature "definition of done" (all languages) |
| `mapping/transactions.md` | 360X transaction ⟷ FHIR `Task` state + v2 bindings |
| `mapping/content.md` | C-CDA ⟷ FHIR section/entry mapping |
| `mapping/loss-profile.md` | dental data that degrades on the 360X bridge |
| `mapping/attachments.md` | supporting images/documents: embed vs. capability link, the 3 cases |
| `use-cases/*` | runnable end-to-end scenarios (UC01, UC03, …) |
| `fixtures/` | canonical synthetic test data shared by all implementations |

**Rule:** if behavior differs between languages, the `spec/` is right and the code
is wrong. Changes to behavior change `spec/` first, then the implementations.
