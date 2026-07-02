using System.Net;
using System.Text;
using System.Text.Json.Nodes;

namespace Ode.Adapter;

/// <summary>Layer 3 (outbound) — FHIR result -> C-CDA Consultation Note. THE LOSS PROFILE
/// LIVES HERE. Faithful port of fhir_to_ccda.py.</summary>
public static class FhirToCcda
{
    private static string Ts() => DateTime.UtcNow.ToString("yyyyMMddHHmmss");
    private static string Esc(string? s) => WebUtility.HtmlEncode(s ?? "");

    public static (string Cda, List<string> LossNotes) BuildConsultationNote(
        JsonObject? patient, IList<JsonObject> resultResources, bool interim = false)
    {
        var sections = new List<string>();
        var lossNotes = new List<string>();
        var lines = new List<string>();
        var dentalLines = new List<string>();

        foreach (var res in resultResources)
        {
            var rtype = JsonX.Str(res["resourceType"]) ?? "";
            var code = JsonX.Obj(res["code"]) ?? new JsonObject();
            var display = JsonX.Str(code["text"]) ?? FirstDisplay(code);

            if (rtype == "Procedure")
            {
                if (IsDental(code))
                {
                    var tooth = ToothText(res);
                    dentalLines.Add($"Dental procedure (CDT): {display}{tooth}");
                    lossNotes.Add($"CDT procedure '{display}' -> narrative only");
                }
                else lines.Add($"Procedure: {display}");
            }
            else if (rtype == "Observation" && IsPerio(res))
            {
                dentalLines.Add($"Periodontal finding: {display} = {ObsValue(res)}");
                lossNotes.Add($"Periodontal observation '{display}' -> narrative only");
            }
            else if (rtype == "ClinicalImpression")
            {
                lines.Add("Assessment: " + (JsonX.Str(res["summary"]) ?? display));
            }
            else if (rtype == "CarePlan")
            {
                lines.Add("Plan: " + (JsonX.Str(res["description"]) ?? display));
            }
        }

        var assessText = ListToXhtml(lines.Count > 0 ? lines : new List<string> { "Referral completed." });
        sections.Add(SectionXml("51847-2", "Assessment and Plan", "Assessment and Plan", assessText));

        if (dentalLines.Count > 0)
        {
            var dl = new List<string>
            {
                "NOTE: The following dental-specific findings are conveyed as narrative; " +
                "structured representation is available via ODE Native (FHIR)."
            };
            dl.AddRange(dentalLines);
            sections.Add(SectionXml("34109-9", "Note", "Dental Findings (narrative)", ListToXhtml(dl)));
        }

        var title = interim ? "Interim Consultation Note" : "Referral Outcome — Consultation Note";
        var cda = DocumentXml(Config.DocConsultationNote, "Consultation Note", Esc(title),
                              Ts(), PatientFragment(patient), Esc(Config.Settings.AdapterId),
                              string.Join("\n", sections));
        return (cda, lossNotes);
    }

    // ------------------------------- helpers -------------------------------- //
    private static string FirstDisplay(JsonObject code)
    {
        var coding = JsonX.Arr(code["coding"]);
        if (coding != null)
            foreach (var c in coding)
            {
                var d = JsonX.Str((c as JsonObject)?["display"]);
                if (!string.IsNullOrEmpty(d)) return d;
            }
        return "";
    }

    private static bool IsDental(JsonObject code)
    {
        var coding = JsonX.Arr(code["coding"]);
        if (coding == null) return false;
        foreach (var c in coding)
            if (JsonX.Str((c as JsonObject)?["system"]) == Config.SysCdt) return true;
        return false;
    }

    private static bool IsPerio(JsonObject res)
    {
        var profiles = JsonX.Arr(JsonX.Obj(res["meta"])?["profile"]);
        if (profiles != null)
            foreach (var p in profiles)
            {
                var s = JsonX.Str(p);
                if (s != null && s.ToLowerInvariant().Contains("perio")) return true;
            }
        return JsonX.Bool(res["_dental_perio"]);
    }

    private static string ToothText(JsonObject res)
    {
        var bs = JsonX.Arr(res["bodySite"]);
        if (bs != null)
            foreach (var b in bs)
            {
                var t = JsonX.Str((b as JsonObject)?["text"]);
                if (!string.IsNullOrEmpty(t)) return $" (tooth {t})";
            }
        return "";
    }

    private static string ObsValue(JsonObject res)
    {
        var vq = JsonX.Obj(res["valueQuantity"]);
        if (vq != null)
        {
            string valStr = "";
            if (vq["value"] is JsonValue jv)
            {
                if (jv.TryGetValue<long>(out var l)) valStr = l.ToString();
                else if (jv.TryGetValue<int>(out var i)) valStr = i.ToString();
                else if (jv.TryGetValue<double>(out var d))
                    valStr = d % 1 == 0 ? ((long)d).ToString() : d.ToString();
                else if (jv.TryGetValue<string>(out var s)) valStr = s;
            }
            var unit = JsonX.Str(vq["unit"]) ?? "";
            return $"{valStr} {unit}".Trim();
        }
        return JsonX.Str(res["valueString"]) ?? "";
    }

    private static string PatientFragment(JsonObject? patient)
    {
        if (patient == null) return "<id nullFlavor=\"UNK\"/>";
        var identArr = JsonX.Arr(patient["identifier"]);
        var ident = identArr is { Count: > 0 } ? JsonX.Obj(identArr[0]) : null;
        var nameArr = JsonX.Arr(patient["name"]);
        var name = nameArr is { Count: > 0 } ? JsonX.Obj(nameArr[0]) : null;
        var givenArr = JsonX.Arr(name?["given"]);
        var given = givenArr != null ? string.Join(" ", givenArr.Select(g => JsonX.Str(g) ?? "")) : "";
        var system = JsonX.Str(ident?["system"]) ?? "urn:ohia";
        var value = JsonX.Str(ident?["value"]) ?? "";
        var family = JsonX.Str(name?["family"]) ?? "";
        return $"<id root=\"{Esc(system)}\" extension=\"{Esc(value)}\"/>" +
               $"<patient><name><given>{Esc(given)}</given>" +
               $"<family>{Esc(family)}</family></name></patient>";
    }

    private static string ListToXhtml(IEnumerable<string> items)
    {
        // Trailing newline becomes each <item>'s tail text, so a parser reading the
        // element value sees one line per item (items aren't run together on round-trip).
        var lis = string.Concat(items.Select(i => $"<item>{Esc(i)}</item>\n"));
        return $"<list>{lis}</list>";
    }

    private static string SectionXml(string code, string name, string title, string text) =>
$@"    <component><section>
      <code code=""{code}"" codeSystem=""2.16.840.1.113883.6.1"" displayName=""{name}""/>
      <title>{title}</title>
      <text>{text}</text>
    </section></component>";

    private static string DocumentXml(string docCode, string docName, string title, string ts,
                                      string patient, string adapter, string sections) =>
$@"<?xml version=""1.0"" encoding=""UTF-8""?>
<ClinicalDocument xmlns=""urn:hl7-org:v3"">
  <realmCode code=""US""/>
  <typeId root=""2.16.840.1.113883.1.3"" extension=""POCD_HD000040""/>
  <templateId root=""2.16.840.1.113883.10.20.22.1.4""/>
  <code code=""{docCode}"" codeSystem=""2.16.840.1.113883.6.1"" displayName=""{docName}""/>
  <title>{title}</title>
  <effectiveTime value=""{ts}""/>
  <confidentialityCode code=""N"" codeSystem=""2.16.840.1.113883.5.25""/>
  <recordTarget><patientRole>{patient}</patientRole></recordTarget>
  <author><time value=""{ts}""/><assignedAuthor>
    <id root=""urn:ohia"" extension=""{adapter}""/>
    <assignedAuthoringDevice><softwareName>{adapter}</softwareName></assignedAuthoringDevice>
  </assignedAuthor></author>
  <component><structuredBody>
{sections}
  </structuredBody></component>
</ClinicalDocument>
";

    // --------------------------------------------------------------------- //
    // Rich Referral Note (referral-grade: diagnose + treat + bill + providers)
    // --------------------------------------------------------------------- //
    private static readonly IReadOnlyDictionary<string, string> SystemOid =
        new Dictionary<string, string>
        {
            [Config.SysIcd10] = "2.16.840.1.113883.6.90",
            [Config.SysSnomed] = "2.16.840.1.113883.6.96",
            [Config.SysCpt] = "2.16.840.1.113883.6.12",
            [Config.SysHcpcs] = "2.16.840.1.113883.6.285",
            [Config.SysLoinc] = "2.16.840.1.113883.6.1",
            [Config.SysRxNorm] = "2.16.840.1.113883.6.88",
            [Config.SysCdt] = "2.16.840.1.113883.6.13",
        };

    private static string DxSystem(string key) => key switch
    {
        "icd10" or "icd-10" => Config.SysIcd10,
        "snomed" => Config.SysSnomed,
        "cdt" => Config.SysCdt,
        _ => Config.SysIcd10,
    };

    private static string SvcSystem(string key) => key switch
    {
        "cpt" => Config.SysCpt,
        "hcpcs" => Config.SysHcpcs,
        "loinc" => Config.SysLoinc,
        "cdt" => Config.SysCdt,
        "snomed" => Config.SysSnomed,
        _ => Config.SysCpt,
    };

    private static string MedSystem(string key) => key == "snomed" ? Config.SysSnomed : Config.SysRxNorm;

    /// <summary>Build a 360X Referral Request C-CDA (Referral Note, LOINC 57133-1) from the
    /// structured referral intake. Mirror of fhir_to_ccda.build_referral_note(rich=...).</summary>
    public static (string Cda, List<string> LossNotes) BuildReferralNote(JsonObject rich)
    {
        var lossNotes = new List<string>();
        var sections = new List<string>();

        var priority = JsonX.Str(rich["priority"]) ?? "routine";
        var reason = JsonX.Str(rich["reason_text"]) ?? "Referral for evaluation.";
        sections.Add(SectionXml("42349-1", "Reason for Referral", "Reason for Referral",
            ListToXhtml(new[] { $"Priority: {priority}", reason })));

        // Problems / diagnoses (coded; ICD-10 for diagnose + bill).
        var problemEntries = new List<string>();
        var problemLines = new List<string>();
        var dentalLines = new List<string>();
        foreach (var dxNode in JsonX.Arr(rich["diagnoses"]) ?? new JsonArray())
        {
            var dx = dxNode as JsonObject;
            if (dx == null) continue;
            var code = JsonX.Str(dx["code"]);
            var display = JsonX.Str(dx["display"]) ?? code ?? "";
            var sysKey = (JsonX.Str(dx["system"]) ?? "icd10").ToLowerInvariant();
            if (sysKey == "cdt")
            {
                dentalLines.Add($"Dental diagnosis (CDT): {display}");
                lossNotes.Add($"Dental diagnosis '{display}' -> narrative only");
                continue;
            }
            var fhirSys = DxSystem(sysKey);
            var oid = SystemOid.TryGetValue(fhirSys, out var o) ? o : "2.16.840.1.113883.6.90";
            if (!string.IsNullOrEmpty(code)) problemEntries.Add(CodedObservationEntry(code, oid, display));
            problemLines.Add(string.IsNullOrEmpty(code) ? display : $"{display} ({code})");
        }
        if (problemLines.Count > 0 || problemEntries.Count > 0)
            sections.Add(SectionWithEntries("11450-4", "Problem List", "Problems / Diagnoses",
                problemLines.Count > 0 ? problemLines : new List<string> { "See coded entries." }, problemEntries));

        // Current medications (supporting clinical context).
        var medEntries = new List<string>();
        var medLines = new List<string>();
        foreach (var medNode in JsonX.Arr(rich["medications"]) ?? new JsonArray())
        {
            var med = medNode as JsonObject;
            if (med == null) continue;
            var code = JsonX.Str(med["code"]);
            var display = JsonX.Str(med["display"]) ?? code ?? "";
            var sysKey = (JsonX.Str(med["system"]) ?? "rxnorm").ToLowerInvariant();
            var fhirSys = MedSystem(sysKey);
            var oid = SystemOid.TryGetValue(fhirSys, out var o) ? o : "2.16.840.1.113883.6.88";
            if (!string.IsNullOrEmpty(code)) medEntries.Add(MedicationEntry(code, oid, display));
            medLines.Add(string.IsNullOrEmpty(code) ? display : $"{display} ({code})");
        }
        if (medLines.Count > 0 || medEntries.Count > 0)
            sections.Add(SectionWithEntries("10160-0", "Medications", "Current Medications",
                medLines.Count > 0 ? medLines : new List<string> { "See coded entries." }, medEntries));

        // Plan of treatment: the requested service (treat + bill).
        var svc = JsonX.Obj(rich["service"]);
        if (svc != null && (!string.IsNullOrEmpty(JsonX.Str(svc["code"])) || !string.IsNullOrEmpty(JsonX.Str(svc["display"]))))
        {
            var sysKey = (JsonX.Str(svc["system"]) ?? "cpt").ToLowerInvariant();
            var fhirSys = SvcSystem(sysKey);
            var oid = SystemOid.TryGetValue(fhirSys, out var o) ? o : "2.16.840.1.113883.6.12";
            var svcCode = JsonX.Str(svc["code"]);
            var svcDisplay = JsonX.Str(svc["display"]);
            var entry = RequestedServiceEntry(svcCode, oid, svcDisplay);
            var line = $"Requested: {svcDisplay ?? svcCode}" + (string.IsNullOrEmpty(svcCode) ? "" : $" ({svcCode})");
            sections.Add(SectionWithEntries("18776-5", "Plan of Treatment", "Requested Service",
                new List<string> { line }, new List<string> { entry }));
        }

        // Payers / coverage (bill).
        var cov = JsonX.Obj(rich["coverage"]);
        if (cov != null)
        {
            var covLines = new List<string>();
            void Add(string label, string key) { var v = JsonX.Str(cov[key]); if (!string.IsNullOrEmpty(v)) covLines.Add($"{label}: {v}"); }
            Add("Payer", "payer"); Add("Member ID", "member_id"); Add("Group", "group");
            Add("Plan", "plan"); Add("Relationship", "relationship");
            if (covLines.Count > 0)
                sections.Add(SectionXml("48768-6", "Payers", "Insurance / Coverage", ListToXhtml(covLines)));
        }

        // Clinical information / justification.
        var supporting = JsonX.Str(rich["supporting_info"]);
        if (!string.IsNullOrEmpty(supporting))
            sections.Add(SectionXml("10164-2", "History of Present Illness", "Clinical Information",
                ListToXhtml(new[] { supporting })));

        if (dentalLines.Count > 0)
        {
            var dl = new List<string>
            { "NOTE: dental-specific findings conveyed as narrative; structured representation is available via ODE Native (FHIR)." };
            dl.AddRange(dentalLines);
            sections.Add(SectionXml("34109-9", "Note", "Dental Findings (narrative)", ListToXhtml(dl)));
        }

        var cda = ReferralDocumentXml(Config.DocReferralNote, Ts(),
            RichPatientFragment(JsonX.Obj(rich["patient"]) ?? new JsonObject()),
            ProviderFragment(JsonX.Obj(rich["referring_provider"]) ?? new JsonObject(), author: true),
            ProviderFragment(JsonX.Obj(rich["rendering_provider"]) ?? new JsonObject(), author: false),
            string.Join("\n", sections));
        return (cda, lossNotes);
    }

    private static string RichPatientFragment(JsonObject p)
    {
        var frags = new List<string>
        { $"<id root=\"http://hospital.example.org/mrn\" extension=\"{Esc(JsonX.Str(p["mrn"]))}\"/>" };
        var addr = JsonX.Obj(p["address"]);
        if (addr != null && new[] { "line", "city", "state", "postalCode" }.Any(k => !string.IsNullOrEmpty(JsonX.Str(addr[k]))))
        {
            var sb = new StringBuilder("<addr>");
            if (!string.IsNullOrEmpty(JsonX.Str(addr["line"]))) sb.Append($"<streetAddressLine>{Esc(JsonX.Str(addr["line"]))}</streetAddressLine>");
            if (!string.IsNullOrEmpty(JsonX.Str(addr["city"]))) sb.Append($"<city>{Esc(JsonX.Str(addr["city"]))}</city>");
            if (!string.IsNullOrEmpty(JsonX.Str(addr["state"]))) sb.Append($"<state>{Esc(JsonX.Str(addr["state"]))}</state>");
            if (!string.IsNullOrEmpty(JsonX.Str(addr["postalCode"]))) sb.Append($"<postalCode>{Esc(JsonX.Str(addr["postalCode"]))}</postalCode>");
            sb.Append("</addr>");
            frags.Add(sb.ToString());
        }
        var phone = JsonX.Str(p["phone"]);
        if (!string.IsNullOrEmpty(phone)) frags.Add($"<telecom value=\"tel:{Esc(phone)}\"/>");
        var gender = (JsonX.Str(p["gender"]) ?? "").ToLowerInvariant() switch { "male" => "M", "female" => "F", _ => "UN" };
        var bd = (JsonX.Str(p["birthDate"]) ?? "").Replace("-", "");
        frags.Add($"<patient><name><given>{Esc(JsonX.Str(p["given"]))}</given>" +
                  $"<family>{Esc(JsonX.Str(p["family"]))}</family></name>" +
                  $"<administrativeGenderCode code=\"{gender}\"/>" +
                  (bd.Length > 0 ? $"<birthTime value=\"{Esc(bd)}\"/>" : "") + "</patient>");
        return string.Concat(frags);
    }

    private static string ProviderFragment(JsonObject pr, bool author)
    {
        var frags = new List<string>();
        var npi = JsonX.Str(pr["npi"]);
        frags.Add(!string.IsNullOrEmpty(npi)
            ? $"<id root=\"{Config.SysNpi}\" extension=\"{Esc(npi)}\"/>" : "<id nullFlavor=\"UNK\"/>");
        var specialty = JsonX.Str(pr["specialty"]);
        if (!string.IsNullOrEmpty(specialty))
            frags.Add($"<code codeSystem=\"2.16.840.1.113883.6.101\" displayName=\"{Esc(specialty)}\"/>");
        var name = $"<name>{Esc(JsonX.Str(pr["name"]) ?? "Unknown Provider")}</name>";
        var org = JsonX.Str(pr["organization"]);
        if (author)
        {
            frags.Add($"<assignedPerson>{name}</assignedPerson>");
            if (!string.IsNullOrEmpty(org))
                frags.Add($"<representedOrganization><name>{Esc(org)}</name></representedOrganization>");
        }
        else
        {
            frags.Add($"<informationRecipient>{name}</informationRecipient>");
            if (!string.IsNullOrEmpty(org))
                frags.Add($"<receivedOrganization><name>{Esc(org)}</name></receivedOrganization>");
        }
        return string.Concat(frags);
    }

    private static string CodedObservationEntry(string code, string oid, string display) =>
        $"<entry><observation classCode=\"OBS\" moodCode=\"EVN\">" +
        $"<value code=\"{Esc(code)}\" codeSystem=\"{oid}\" displayName=\"{Esc(display)}\"/></observation></entry>";

    private static string RequestedServiceEntry(string? code, string oid, string? display)
    {
        var codeAttr = string.IsNullOrEmpty(code) ? "" : $"code=\"{Esc(code)}\" codeSystem=\"{oid}\" ";
        return $"<entry><procedure classCode=\"PROC\" moodCode=\"RQO\">" +
               $"<code {codeAttr}displayName=\"{Esc(display ?? "")}\"/></procedure></entry>";
    }

    private static string MedicationEntry(string? code, string oid, string? display)
    {
        var codeAttr = string.IsNullOrEmpty(code) ? "" : $"code=\"{Esc(code)}\" codeSystem=\"{oid}\" ";
        return "<entry><substanceAdministration classCode=\"SBADM\" moodCode=\"EVN\">" +
               "<consumable><manufacturedProduct><manufacturedMaterial>" +
               $"<code {codeAttr}displayName=\"{Esc(display ?? "")}\"/>" +
               "</manufacturedMaterial></manufacturedProduct></consumable></substanceAdministration></entry>";
    }

    private static string SectionWithEntries(string code, string name, string title,
                                             List<string> lines, List<string> entries) =>
$@"    <component><section>
      <code code=""{code}"" codeSystem=""2.16.840.1.113883.6.1"" displayName=""{Esc(name)}""/>
      <title>{Esc(title)}</title>
      <text>{ListToXhtml(lines)}</text>
      {string.Concat(entries)}
    </section></component>";

    private static string ReferralDocumentXml(string docCode, string ts, string patient,
                                              string referring, string rendering, string sections) =>
$@"<?xml version=""1.0"" encoding=""UTF-8""?>
<ClinicalDocument xmlns=""urn:hl7-org:v3"">
  <realmCode code=""US""/>
  <typeId root=""2.16.840.1.113883.1.3"" extension=""POCD_HD000040""/>
  <templateId root=""2.16.840.1.113883.10.20.22.1.4""/>
  <code code=""{docCode}"" codeSystem=""2.16.840.1.113883.6.1"" displayName=""Referral Note""/>
  <title>Referral Request</title>
  <effectiveTime value=""{ts}""/>
  <confidentialityCode code=""N"" codeSystem=""2.16.840.1.113883.5.25""/>
  <recordTarget><patientRole>{patient}</patientRole></recordTarget>
  <author><time value=""{ts}""/><assignedAuthor>{referring}</assignedAuthor></author>
  <informationRecipient><intendedRecipient>{rendering}</intendedRecipient></informationRecipient>
  <component><structuredBody>
{sections}
  </structuredBody></component>
</ClinicalDocument>
";
}
