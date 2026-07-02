"""Configuration for the 360/ODE Adapter.

Values can be overridden via environment variables (prefix ODE_ADAPTER_).
"""
from __future__ import annotations

from dataclasses import dataclass
import os


# --- Pinned dependency versions (see README / architecture spec section 3.9) ---
COW_VERSION = "1.0.0-ballot"          # Clinical Order Workflows (O&O); confirm at pin time
CCDA_ON_FHIR_VERSION = "2.0.0"        # for the C-CDA <-> US Core mappings
US_CORE_VERSION = "6.1.0"
IHE_360X_VERSION = "1.2 (2021-04-14) + US national extension"

# --- Terminology systems ---
SYS_CDT = "http://www.ada.org/cdt"
SYS_LOINC = "http://loinc.org"
SYS_SNOMED = "http://snomed.info/sct"
SYS_SNODENT = "http://www.ada.org/snodent"
SYS_NPI = "http://hl7.org/fhir/sid/us-npi"
SYS_ICD10 = "http://hl7.org/fhir/sid/icd-10-cm"     # diagnoses (diagnose + bill)
SYS_RXNORM = "http://www.nlm.nih.gov/research/umls/rxnorm"  # medications
SYS_CPT = "http://www.ama-assn.org/go/cpt"          # medical procedures/services
SYS_HCPCS = "urn:oid:2.16.840.1.113883.6.285"       # HCPCS Level II services
SYS_NUCC = "http://nucc.org/provider-taxonomy"      # provider specialty/taxonomy
SYS_HL7_ACT_CODE = "http://terminology.hl7.org/CodeSystem/v3-ActCode"

# OID -> FHIR system for inbound C-CDA coded elements.
OID_TO_SYSTEM = {
    "2.16.840.1.113883.6.96": SYS_SNOMED,
    "2.16.840.1.113883.6.1": SYS_LOINC,
    "2.16.840.1.113883.6.88": SYS_RXNORM,           # RxNorm (medications)
    "2.16.840.1.113883.6.90": SYS_ICD10,            # ICD-10-CM
    "2.16.840.1.113883.6.12": SYS_CPT,              # CPT-4
    "2.16.840.1.113883.6.285": SYS_HCPCS,           # HCPCS Level II
    "2.16.840.1.113883.6.13": SYS_CDT,              # CDT
    "2.16.840.1.113883.6.101": SYS_NUCC,            # NUCC taxonomy
}

# --- LOINC document type codes (C-CDA on FHIR document profiles) ---
DOC_REFERRAL_NOTE = "57133-1"         # Referral Note  -> inbound PCC-55
DOC_CONSULTATION_NOTE = "11488-4"     # Consultation Note -> outbound PCC-57 / PCC-59

# --- COW subset (scoped to what 360X supports; see spec/mapping/360x-cow-crosswalk.md) ---
# Referral-id loop key (system used on ServiceRequest.identifier / Task.identifier).
SYS_REFERRAL_ID = "urn:ohia:referral-id"
# ODE referral sub-status axis — the granular, 360X-driven progress axis layered on
# top of Task.status. This is the CodeSystem the ODE contract (openapi.yaml) uses for
# Task.businessStatus. `received`/`scheduled` are the contract's example codes; the
# rest are the additional 360X-driven states this bridge tracks.
COW_BUSINESS_STATUS_SYSTEM = "http://ohia-codes.org/CodeSystem/ode-referral-sub-status"
COW_BUSINESS_STATUS = {
    "received":           "Received",
    "scheduled":          "Scheduled",
    "referral-sent":      "Referral sent",
    "accepted":           "Accepted by fulfiller",
    "declined":           "Declined by fulfiller",
    "appointment-booked": "Appointment booked",
    "appointment-noshow": "Appointment no-show",
    "interim-results":    "Interim results available",
    "outcome-final":      "Final outcome available",
    "cancelled":          "Cancelled by initiator",
}
# --- Reply-content value sets (scoped to the six 360X replies) ---
# Coded decline reasons -> Task.statusReason (PCC-56 decline). Provisional.
DECLINE_REASON_SYSTEM = "urn:ohia:cow:decline-reason"
DECLINE_REASONS = {
    "out-of-network":    "Provider out of network for this plan",
    "wrong-specialty":   "Wrong specialty for the requested service",
    "insufficient-info": "Insufficient clinical information to accept",
    "capacity":          "No capacity / unable to schedule in time",
    "patient-declined":  "Patient declined the referral",
}
# Appointment types -> Appointment.appointmentType (PCC-60). HL7 v2-0276-ish.
APPOINTMENT_TYPE_SYSTEM = "http://terminology.hl7.org/CodeSystem/v2-0276"
APPOINTMENT_TYPES = {
    "in-person": "In-person visit",
    "tele":      "Telehealth visit",
}
# No-show / reschedule reasons -> Communication.reasonCode (PCC-61). Provisional.
NOSHOW_REASON_SYSTEM = "urn:ohia:cow:noshow-reason"
NOSHOW_REASONS = {
    "no-show":        "Patient did not attend the scheduled appointment",
    "cancelled-late": "Patient cancelled too late to rebook",
    "transport":      "Patient had no transportation",
}
# Clearance / disposition value set -> outcome Observation (PCC-57). Provisional.
CLEARANCE_SYSTEM = "urn:ohia:cow:clearance-disposition"
CLEARANCE_LOINC = "11536-2"   # Hospital discharge Dx (illustrative outcome finding)
CLEARANCE_DISPOSITIONS = {
    "cleared":     "Cleared for treatment",
    "not-cleared": "Not cleared for treatment",
    "partial":     "Partially cleared / conditional",
}

# ODE profile canonical URLs (match the ODE Native contract in openapi.yaml — domain
# oralhealthalliance.net). Unpublished ballot artifacts; flagged provisional.
_ODE_SD = "https://oralhealthalliance.net/fhir/StructureDefinition"

# The referral order (ServiceRequest) — three directional profiles, one per direction
# that involves dental. Each carries its own must-support (see the crosswalk).
PROFILE_ODE_MED_TO_DENTAL = f"{_ODE_SD}/ode-medical-to-dental-referral"
PROFILE_ODE_DENTAL_TO_DENTAL = f"{_ODE_SD}/ode-dental-to-dental-referral"
PROFILE_ODE_DENTAL_TO_MEDICAL = f"{_ODE_SD}/ode-dental-to-medical-referral"
REFERRAL_PROFILE_BY_DIRECTION = {
    "medical-to-dental": PROFILE_ODE_MED_TO_DENTAL,
    "dental-to-dental":  PROFILE_ODE_DENTAL_TO_DENTAL,
    "dental-to-medical": PROFILE_ODE_DENTAL_TO_MEDICAL,
}
# Directions where the *receiving* clinician acts/bills in the medical world: CDT is
# NOT must-support; ICD-10-CM reasonCode + CPT/HCPCS code are.
MEDICAL_SIDE_DIRECTIONS = {"medical-to-dental", "dental-to-medical"}
DEFAULT_DIRECTION = "medical-to-dental"

# The COW workflow object (Task) and the medication list (List) inherit base FHIR.
PROFILE_ODE_REFERRAL_TASK = f"{_ODE_SD}/ode-referral-task"
PROFILE_ODE_MEDICATION_LIST = f"{_ODE_SD}/ode-medication-list"
MED_LIST_LOINC = "10160-0"   # History of Medication use Narrative (List.code)

# Provisional ODE dental profile canonicals (dental-flavored resources).
PROFILE_COW_TASK = PROFILE_ODE_REFERRAL_TASK
PROFILE_DENTAL_PROCEDURE = f"{_ODE_SD}/ode-dental-procedure"
PROFILE_PERIO_OBSERVATION = f"{_ODE_SD}/ode-perio-observation"

# --- LOINC section codes used for section-level mapping (C-CDA <-> US Core) ---
SECTION_LOINC = {
    "11450-4": "problems",            # -> Condition
    "10160-0": "medications",         # -> MedicationRequest
    "48765-2": "allergies",           # -> AllergyIntolerance
    "30954-2": "results",             # -> Observation / DiagnosticReport
    "47519-4": "procedures",          # -> Procedure
    "42349-1": "reason_for_referral", # -> ServiceRequest.reasonCode + priority
    "18776-5": "plan_of_treatment",   # -> ServiceRequest.code (requested service)
    "48768-6": "payers",              # -> Coverage (insurance / billing)
    "10164-2": "clinical_info",       # -> ServiceRequest.note (HPI / supporting)
}


@dataclass
class Settings:
    # Base URL of the ODE Native (FHIR R4) server this adapter drives.
    ode_native_base_url: str = os.getenv(
        "ODE_ADAPTER_ODE_BASE_URL", "http://localhost:8080/fhir")
    # When true, the FHIR backend does not perform HTTP; it echoes the bundle with
    # assigned ids. Lets the demo run with no live FHIR server.
    dry_run: bool = os.getenv("ODE_ADAPTER_DRY_RUN", "true").lower() == "true"
    # --- Plugin selection (plug and play) ---
    fhir_backend: str = os.getenv("ODE_ADAPTER_FHIR_BACKEND", "generic-r4")
    ihe_codec: str = os.getenv("ODE_ADAPTER_IHE_CODEC", "json-envelope")
    ihe_transport: str = os.getenv("ODE_ADAPTER_IHE_TRANSPORT", "capture")
    # Adapter's own identifier, recorded in Provenance/AuditEvent.
    adapter_id: str = os.getenv("ODE_ADAPTER_ID", "ohia-360ode-adapter")
    # Where the `http` outbound transport POSTs packaged 360X messages — point this
    # at the stub 360X server (tools/stub_360x_server.py) or a real receiver.
    ihe_outbound_url: str = os.getenv(
        "ODE_ADAPTER_IHE_OUTBOUND_URL", "http://localhost:9000/360x/receive")
    request_timeout_seconds: float = 30.0


settings = Settings()
