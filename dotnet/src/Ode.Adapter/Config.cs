namespace Ode.Adapter;

/// <summary>Configuration + pinned constants. Mirrors config.py. Env overrides use ODE_ADAPTER_*.</summary>
public static class Config
{
    // Pinned dependency versions (see ARCHITECTURE / spec).
    public const string CowVersion = "1.0.0-ballot";
    public const string CcdaOnFhirVersion = "2.0.0";
    public const string UsCoreVersion = "6.1.0";
    public const string Ihe360XVersion = "1.2 (2021-04-14) + US national extension";

    // Terminology systems.
    public const string SysCdt = "http://www.ada.org/cdt";
    public const string SysLoinc = "http://loinc.org";
    public const string SysSnomed = "http://snomed.info/sct";
    public const string SysSnodent = "http://www.ada.org/snodent";
    public const string SysNpi = "http://hl7.org/fhir/sid/us-npi";
    public const string SysIcd10 = "http://hl7.org/fhir/sid/icd-10-cm";     // diagnoses
    public const string SysRxNorm = "http://www.nlm.nih.gov/research/umls/rxnorm";  // meds
    public const string SysCpt = "http://www.ama-assn.org/go/cpt";          // medical procedures
    public const string SysHcpcs = "urn:oid:2.16.840.1.113883.6.285";       // HCPCS Level II
    public const string SysNucc = "http://nucc.org/provider-taxonomy";      // provider specialty
    public const string SysHl7ActCode = "http://terminology.hl7.org/CodeSystem/v3-ActCode";

    // OID -> FHIR system for inbound C-CDA coded elements.
    public static readonly IReadOnlyDictionary<string, string> OidToSystem =
        new Dictionary<string, string>
        {
            ["2.16.840.1.113883.6.96"] = SysSnomed,
            ["2.16.840.1.113883.6.1"] = SysLoinc,
            ["2.16.840.1.113883.6.88"] = SysRxNorm,     // RxNorm (medications)
            ["2.16.840.1.113883.6.90"] = SysIcd10,      // ICD-10-CM
            ["2.16.840.1.113883.6.12"] = SysCpt,        // CPT-4
            ["2.16.840.1.113883.6.285"] = SysHcpcs,     // HCPCS Level II
            ["2.16.840.1.113883.6.13"] = SysCdt,        // CDT
            ["2.16.840.1.113883.6.101"] = SysNucc,      // NUCC taxonomy
        };

    // LOINC document type codes.
    public const string DocReferralNote = "57133-1";       // inbound PCC-55
    public const string DocConsultationNote = "11488-4";   // outbound PCC-57 / PCC-59

    // Referral-id loop key (system on ServiceRequest.identifier / Task.identifier).
    public const string SysReferralId = "urn:ohia:referral-id";

    // ODE referral sub-status axis — the granular 360X-driven progress axis layered on
    // top of Task.status. The CodeSystem the ODE contract uses for Task.businessStatus.
    public const string CowBusinessStatusSystem =
        "http://ohia-codes.org/CodeSystem/ode-referral-sub-status";
    public static readonly IReadOnlyDictionary<string, string> CowBusinessStatus =
        new Dictionary<string, string>
        {
            ["received"] = "Received",
            ["scheduled"] = "Scheduled",
            ["referral-sent"] = "Referral sent",
            ["accepted"] = "Accepted by fulfiller",
            ["declined"] = "Declined by fulfiller",
            ["appointment-booked"] = "Appointment booked",
            ["appointment-noshow"] = "Appointment no-show",
            ["interim-results"] = "Interim results available",
            ["outcome-final"] = "Final outcome available",
            ["cancelled"] = "Cancelled by initiator",
        };

    // Reply-content value sets (scoped to the six 360X replies).
    public const string DeclineReasonSystem = "urn:ohia:cow:decline-reason";
    public static readonly IReadOnlyDictionary<string, string> DeclineReasons =
        new Dictionary<string, string>
        {
            ["out-of-network"] = "Provider out of network for this plan",
            ["wrong-specialty"] = "Wrong specialty for the requested service",
            ["insufficient-info"] = "Insufficient clinical information to accept",
            ["capacity"] = "No capacity / unable to schedule in time",
            ["patient-declined"] = "Patient declined the referral",
        };
    public const string AppointmentTypeSystem = "http://terminology.hl7.org/CodeSystem/v2-0276";
    public static readonly IReadOnlyDictionary<string, string> AppointmentTypes =
        new Dictionary<string, string>
        {
            ["in-person"] = "In-person visit",
            ["tele"] = "Telehealth visit",
        };
    public const string NoshowReasonSystem = "urn:ohia:cow:noshow-reason";
    public static readonly IReadOnlyDictionary<string, string> NoshowReasons =
        new Dictionary<string, string>
        {
            ["no-show"] = "Patient did not attend the scheduled appointment",
            ["cancelled-late"] = "Patient cancelled too late to rebook",
            ["transport"] = "Patient had no transportation",
        };
    public const string ClearanceSystem = "urn:ohia:cow:clearance-disposition";
    public const string ClearanceLoinc = "11536-2";
    public static readonly IReadOnlyDictionary<string, string> ClearanceDispositions =
        new Dictionary<string, string>
        {
            ["cleared"] = "Cleared for treatment",
            ["not-cleared"] = "Not cleared for treatment",
            ["partial"] = "Partially cleared / conditional",
        };

    // ODE profile canonical URLs (match the ODE Native contract — oralhealthalliance.net).
    private const string OdeSd = "https://oralhealthalliance.net/fhir/StructureDefinition";
    public const string ProfileOdeMedToDental = OdeSd + "/ode-medical-to-dental-referral";
    public const string ProfileOdeDentalToDental = OdeSd + "/ode-dental-to-dental-referral";
    public const string ProfileOdeDentalToMedical = OdeSd + "/ode-dental-to-medical-referral";
    public static readonly IReadOnlyDictionary<string, string> ReferralProfileByDirection =
        new Dictionary<string, string>
        {
            ["medical-to-dental"] = ProfileOdeMedToDental,
            ["dental-to-dental"] = ProfileOdeDentalToDental,
            ["dental-to-medical"] = ProfileOdeDentalToMedical,
        };
    // Directions where the receiving clinician acts/bills medically: no CDT must-support.
    public static readonly IReadOnlySet<string> MedicalSideDirections =
        new HashSet<string> { "medical-to-dental", "dental-to-medical" };
    public const string DefaultDirection = "medical-to-dental";
    public const string ProfileOdeReferralTask = OdeSd + "/ode-referral-task";
    public const string ProfileOdeMedicationList = OdeSd + "/ode-medication-list";
    public const string MedListLoinc = "10160-0";   // History of Medication use Narrative
    public const string ProfileCowTask = ProfileOdeReferralTask;
    public const string ProfileDentalProcedure = OdeSd + "/ode-dental-procedure";
    public const string ProfilePerioObservation = OdeSd + "/ode-perio-observation";

    // LOINC section codes -> kind (C-CDA <-> US Core).
    public static readonly IReadOnlyDictionary<string, string> SectionLoinc =
        new Dictionary<string, string>
        {
            ["11450-4"] = "problems",
            ["10160-0"] = "medications",
            ["48765-2"] = "allergies",
            ["30954-2"] = "results",
            ["47519-4"] = "procedures",
            ["42349-1"] = "reason_for_referral",
            ["18776-5"] = "plan_of_treatment",
            ["48768-6"] = "payers",
            ["10164-2"] = "clinical_info",
        };

    private static string Env(string key, string fallback) =>
        Environment.GetEnvironmentVariable(key) is { Length: > 0 } v ? v : fallback;

    public static class Settings
    {
        public static string OdeNativeBaseUrl =>
            Env("ODE_ADAPTER_ODE_BASE_URL", "http://localhost:8080/fhir");

        public static bool DryRun =>
            Env("ODE_ADAPTER_DRY_RUN", "true").ToLowerInvariant() == "true";

        public static string FhirBackend => Env("ODE_ADAPTER_FHIR_BACKEND", "generic-r4");
        public static string IheCodec => Env("ODE_ADAPTER_IHE_CODEC", "json-envelope");
        public static string IheTransport => Env("ODE_ADAPTER_IHE_TRANSPORT", "capture");
        public static string AdapterId => Env("ODE_ADAPTER_ID", "ohia-360ode-adapter");
        public static string IheOutboundUrl =>
            Env("ODE_ADAPTER_IHE_OUTBOUND_URL", "http://localhost:9000/360x/receive");

        public static double RequestTimeoutSeconds => 30.0;
    }
}
