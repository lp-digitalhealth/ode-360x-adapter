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
    public const string SysRxNorm = "http://www.nlm.nih.gov/research/umls/rxnorm";

    // LOINC document type codes.
    public const string DocReferralNote = "57133-1";       // inbound PCC-55
    public const string DocConsultationNote = "11488-4";   // outbound PCC-57 / PCC-59

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
