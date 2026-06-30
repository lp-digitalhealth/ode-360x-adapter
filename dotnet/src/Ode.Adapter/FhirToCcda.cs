using System.Net;
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
        var lis = string.Concat(items.Select(i => $"<item>{Esc(i)}</item>"));
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
}
