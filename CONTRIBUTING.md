# Contributing

This is a multi-language reference project. OHIA members and the community are
welcome. The golden rule: **implement against `spec/`, not against another
language's code.**

## How to pick up work
1. Find an unchecked box in `spec/contract/conformance-checklist.md` or a
   `good-first-issue`.
2. Implement it in one language (`python/`, `java/`, or `dotnet/`).
3. Validate against the shared `spec/` fixtures and the conformance checklist.
4. Open a PR. CI runs only the affected language's jobs.

## Behavior changes
Change `spec/` first (it is the source of truth), then update implementations to
match. A PR that changes behavior in one language without updating `spec/` will be
asked to update the contract.

## Per-language state
See each directory's `STATUS.md`. Python is the reference; Java and .NET are being
built to parity.
