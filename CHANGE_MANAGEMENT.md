# Change Management & Governance

## Overview

This repository holds the **ODE ↔ 360X adapter**: a multi-language reference
implementation that bridges IHE 360X (HL7 v2 + C-CDA) and ODE Native (FHIR R4 with
oral-health profiles). It is a community reference for Connectathon and standards
testing, maintained under the [`lp-digitalhealth`](https://github.com/lp-digitalhealth)
organization in support of the Oral Health Innovation Alliance (OHIA).

This document describes how changes are proposed, reviewed, and accepted, and how
contributors earn additional responsibility over time. It is adapted from the
meritocratic, consensus-based model used by sibling projects (e.g.
[`cqframework/cql-tests`](https://github.com/cqframework/cql-tests)).

The golden rule for all changes: **implement against `spec/`, not against another
language's code.** `spec/` is the source of truth; the `python/`, `dotnet/`, and
`java/` trees are independent implementations of that contract.

## Roles

### Stakeholders
Anyone with an interest in the project — implementers, EHR/PMS vendors, payers and
providers, standards developers, and testing-event participants. Stakeholder
participation is voluntary. Common contributions include evangelism, feedback from a
new-implementer perspective, and helping other users.

### Contributors
Community members who contribute concretely: filing issues, reporting bugs,
improving docs, or submitting pull requests. There is no commitment expectation and
no selection process — anyone can be a contributor. Contributors submit changes via
pull requests, which are reviewed by committers.

### Committers
Community members who have shown sustained, high-quality engagement and are granted
write access. New committers are nominated by an existing committer and confirmed by
the maintainers (PMC). Committership is a privilege earned through merit; it can be
revoked by the PMC in extreme circumstances but otherwise persists as long as the
committer stays engaged.

### Project Management Committee (PMC)
The individuals with `admin` ("owner") rights on the repository. The PMC is
responsible for the smooth running of the project: roadmap, releases, change
management, governance, and onboarding committers.

- Mark Marciante (Chair / admin)
- *(additional PMC members added here as appointed)*

## Decision-Making

The project operates by **consensus**: if nobody explicitly opposes a proposal and
it aligns with `spec/`, it is considered to have community support. Decisions that
cannot be resolved by consensus (strategic direction, releases, governance) are
settled by a vote of committers and, where needed, the PMC. Only committers and PMC
members have binding votes.

Typical flow: **Proposal → Discussion → (Vote if needed) → Decision.** Proposals
start as GitHub issues; committers may open a pull request directly.

## Contributions & Branch Protection

The project uses a **stable-trunk** methodology: `main` must always be in a
releasable state. Branch protection on `main` enforces this.

**Current rules (enforced on `main`):**
- All changes land through a **pull request** — no direct pushes for non-admins.
- At least **one approving review** is required before merge.
- Stale approvals are **dismissed** when new commits are pushed.
- **Conversation resolution** is required before merge.
- **Force-pushes and branch deletion** are blocked.
- **Admins (PMC) may override** the PR/review requirement when necessary
  (`enforce_admins` is off). This is intended for bootstrapping and emergencies, not
  routine work.

**Scaling the gate as the committer base grows:**
As more committers are onboarded, the PMC will progressively tighten controls,
tracking the sibling-project standard:
1. Raise the required approving reviews to **two committers** who are not the PR
   author (`required_approving_review_count: 2`).
2. Require **code-owner review** via `.github/CODEOWNERS`
   (`require_code_owner_reviews: true`).
3. Gate merges on **CI status checks** once workflows are stable.
4. Eventually apply the rules to **everyone including admins**
   (`enforce_admins: true`).

Large, significant, or breaking changes should have committer consensus before being
applied, even when the mechanical review bar is met.

## Behavior Changes

Change `spec/` first, then update the implementations to match. A PR that changes
behavior in one language without updating the contract will be asked to update
`spec/`. See `CONTRIBUTING.md` for the day-to-day workflow.

## Releases

Packages use semantic versioning. Any stakeholder may propose a release; the PMC
reviews and approves contents and timing, coordinating with impacted stakeholders and
the availability of the specifications involved.

## Communication

Communication should be open and public — primarily GitHub issues and pull requests.
When relevant discussion happens privately, committers should summarize it in a
public channel.

## Continuous Improvement

The PMC and committers review this governance periodically and amend it by consensus.

**Last Formal Review Date: 2026/07/02**
