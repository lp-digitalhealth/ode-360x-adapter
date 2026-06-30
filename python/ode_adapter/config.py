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

# --- LOINC document type codes (C-CDA on FHIR document profiles) ---
DOC_REFERRAL_NOTE = "57133-1"         # Referral Note  -> inbound PCC-55
DOC_CONSULTATION_NOTE = "11488-4"     # Consultation Note -> outbound PCC-57 / PCC-59

# --- LOINC section codes used for section-level mapping (C-CDA <-> US Core) ---
SECTION_LOINC = {
    "11450-4": "problems",            # -> Condition
    "10160-0": "medications",         # -> MedicationRequest
    "48765-2": "allergies",           # -> AllergyIntolerance
    "30954-2": "results",             # -> Observation / DiagnosticReport
    "47519-4": "procedures",          # -> Procedure
    "42349-1": "reason_for_referral", # -> ServiceRequest.reasonCode
    "18776-5": "plan_of_treatment",   # -> CarePlan / ServiceRequest
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
