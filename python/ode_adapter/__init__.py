"""360/ODE Adapter — reference implementation.

A bridging edge server between IHE 360X (closed-loop referral over Direct /
XDM / HL7 v2 / C-CDA) and ODE Native (FHIR R4 realization of the Clinical
Order Workflows framework, with oral-health profiles).

This is an OHIA reference implementation intended for Connectathon use and as a
starting point for production adapters. It will require refactoring; see
README.md, section "What is implemented vs. stubbed."
"""

__version__ = "0.1.0"
