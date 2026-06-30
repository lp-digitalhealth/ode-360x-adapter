using System.Text.Json.Nodes;
using System.Xml.Linq;

namespace Ode.Adapter;

/// <summary>Layer 3 (inbound) — C-CDA Referral Note -> FHIR transaction Bundle.
/// Faithful port of ccda_to_fhir.py. Resources are System.Text.Json JsonObjects.</summary>
public static class CcdaToFhir
{
    private static readonly XNamespace V3 = "urn:hl7-org:v3";

    private sealed record Built(string FullUrl, JsonObject Resource);

    private static string Urn() => $"urn:uuid:{Guid.NewGuid()}";

    private static string Text(XElement? el) => el?.Value.Trim() ?? "";

    private static JsonObject? Coding(XElement? codeEl, string systemDefault = Config.SysSnomed)
    {
        if (codeEl == null) return null;
        var code = codeEl.Attribute("code")?.Value;
        if (string.IsNullOrEmpty(code)) return null;
        var system = (codeEl.Attribute("codeSystem")?.Value) switch
        {
            "2.16.840.1.113883.6.96" => Config.SysSnomed,
            "2.16.840.1.113883.6.1" => Config.SysLoinc,
            "2.16.840.1.113883.6.88" => Config.SysRxNorm,
            _ => systemDefault,
        };
        var coding = new JsonObject { ["system"] = system, ["code"] = code };
        var dn = codeEl.Attribute("displayName")?.Value;
        if (!string.IsNullOrEmpty(dn)) coding["display"] = dn;
        return new JsonObject { ["coding"] = new JsonArray { coding }, ["text"] = dn };
    }

    private static JsonObject Entry(string method, string url, Built b) => new()
    {
        ["fullUrl"] = b.FullUrl,
        ["resource"] = b.Resource,
        ["request"] = new JsonObject { ["method"] = method, ["url"] = url },
    };

    public static JsonObject TransformReferralNote(string cdaXml, string referralId)
    {
        var root = XDocument.Parse(cdaXml).Root!;

        var pr = root.Descendants(V3 + "recordTarget").Elements(V3 + "patientRole").FirstOrDefault();
        var patient = BuildPatient(pr);

        var author = root.Descendants(V3 + "author").Elements(V3 + "assignedAuthor").FirstOrDefault();
        var (practitioner, organization) = BuildAuthor(author);

        var entries = new JsonArray();
        var supportingRefs = new List<JsonObject>();
        var reasonCodes = new List<JsonObject>();

        entries.Add(Entry("POST", "Patient", patient));
        if (practitioner != null) entries.Add(Entry("POST", "Practitioner", practitioner));
        if (organization != null) entries.Add(Entry("POST", "Organization", organization));

        var structuredBody = root.Descendants(V3 + "structuredBody").FirstOrDefault();
        var sections = structuredBody?.Elements(V3 + "component").Elements(V3 + "section")
                       ?? Enumerable.Empty<XElement>();

        foreach (var section in sections)
        {
            var loinc = section.Element(V3 + "code")?.Attribute("code")?.Value;
            if (loinc == null || !Config.SectionLoinc.TryGetValue(loinc, out var kind)) continue;
            var narrative = Text(section.Element(V3 + "text"));

            switch (kind)
            {
                case "problems":
                    foreach (var obs in section.Descendants(V3 + "observation"))
                    {
                        var cond = BuildCondition(obs, patient.FullUrl);
                        if (cond != null)
                        {
                            entries.Add(Entry("POST", "Condition", cond));
                            supportingRefs.Add(new JsonObject { ["reference"] = cond.FullUrl });
                        }
                    }
                    break;
                case "medications":
                    foreach (var sa in section.Descendants(V3 + "substanceAdministration"))
                    {
                        var mr = BuildMedication(sa, patient.FullUrl);
                        if (mr != null) entries.Add(Entry("POST", "MedicationRequest", mr));
                    }
                    break;
                case "allergies":
                    foreach (var obs in section.Descendants(V3 + "observation"))
                    {
                        var ai = BuildAllergy(obs, patient.FullUrl);
                        if (ai != null) entries.Add(Entry("POST", "AllergyIntolerance", ai));
                    }
                    break;
                case "reason_for_referral":
                    var valEl = section.Descendants(V3 + "observation")
                                       .Select(o => o.Element(V3 + "value"))
                                       .FirstOrDefault(x => x != null);
                    var rc = Coding(valEl);
                    if (rc != null) reasonCodes.Add(rc);
                    else reasonCodes.Add(new JsonObject
                    {
                        ["text"] = narrative.Length > 500 ? narrative[..500] : narrative
                    });
                    break;
            }
        }

        var serviceRequest = BuildServiceRequest(referralId, patient.FullUrl,
                                                 practitioner?.FullUrl, reasonCodes, supportingRefs);
        entries.Add(Entry("POST", "ServiceRequest", serviceRequest));

        var task = BuildTask(referralId, patient.FullUrl, serviceRequest.FullUrl);
        entries.Add(Entry("POST", "Task", task));

        var prov = BuildProvenance(new[] { serviceRequest.FullUrl, task.FullUrl });
        entries.Add(Entry("POST", "Provenance", prov));

        return new JsonObject { ["resourceType"] = "Bundle", ["type"] = "transaction", ["entry"] = entries };
    }

    // ----------------------------- builders --------------------------------- //
    private static Built BuildPatient(XElement? pr)
    {
        var res = new JsonObject
        {
            ["resourceType"] = "Patient",
            ["meta"] = new JsonObject
            {
                ["profile"] = new JsonArray
                { "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient" }
            },
        };
        if (pr != null)
        {
            var idel = pr.Element(V3 + "id");
            var ext = idel?.Attribute("extension")?.Value;
            if (!string.IsNullOrEmpty(ext))
                res["identifier"] = new JsonArray { new JsonObject
                {
                    ["system"] = idel!.Attribute("root")?.Value ?? "urn:oid:unknown",
                    ["value"] = ext,
                } };
            var name = pr.Element(V3 + "patient")?.Element(V3 + "name");
            if (name != null)
            {
                var given = new JsonArray();
                foreach (var g in name.Elements(V3 + "given"))
                {
                    var t = g.Value.Trim();
                    if (t.Length > 0) given.Add(t);
                }
                res["name"] = new JsonArray { new JsonObject
                {
                    ["family"] = Text(name.Element(V3 + "family")),
                    ["given"] = given,
                } };
            }
            var gcode = pr.Element(V3 + "patient")?.Element(V3 + "administrativeGenderCode")?.Attribute("code")?.Value;
            if (!string.IsNullOrEmpty(gcode))
                res["gender"] = gcode == "M" ? "male" : gcode == "F" ? "female" : "unknown";
            var bd = pr.Element(V3 + "patient")?.Element(V3 + "birthTime")?.Attribute("value")?.Value;
            if (!string.IsNullOrEmpty(bd) && bd.Length >= 8)
                res["birthDate"] = $"{bd[..4]}-{bd.Substring(4, 2)}-{bd.Substring(6, 2)}";
        }
        return new Built(Urn(), res);
    }

    private static (Built? practitioner, Built? organization) BuildAuthor(XElement? author)
    {
        if (author == null) return (null, null);
        var practitioner = new JsonObject
        {
            ["resourceType"] = "Practitioner",
            ["meta"] = new JsonObject
            {
                ["profile"] = new JsonArray
                { "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner" }
            },
        };
        var ext = author.Element(V3 + "id")?.Attribute("extension")?.Value;
        if (!string.IsNullOrEmpty(ext))
            practitioner["identifier"] = new JsonArray { new JsonObject
            { ["system"] = Config.SysNpi, ["value"] = ext } };
        var name = author.Element(V3 + "assignedPerson")?.Element(V3 + "name");
        if (name != null)
        {
            var given = new JsonArray();
            foreach (var g in name.Elements(V3 + "given"))
            {
                var t = g.Value.Trim();
                if (t.Length > 0) given.Add(t);
            }
            practitioner["name"] = new JsonArray { new JsonObject
            { ["family"] = Text(name.Element(V3 + "family")), ["given"] = given } };
        }
        Built? org = null;
        var orgEl = author.Element(V3 + "representedOrganization");
        if (orgEl != null)
        {
            var orgName = Text(orgEl.Element(V3 + "name"));
            org = new Built(Urn(), new JsonObject
            {
                ["resourceType"] = "Organization",
                ["name"] = orgName.Length > 0 ? orgName : "Referring Organization",
                ["meta"] = new JsonObject
                {
                    ["profile"] = new JsonArray
                    { "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization" }
                },
            });
        }
        return (new Built(Urn(), practitioner), org);
    }

    private static Built? BuildCondition(XElement obs, string patientUrl)
    {
        var code = Coding(obs.Element(V3 + "value"));
        if (code == null) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "Condition",
            ["clinicalStatus"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                {
                    ["system"] = "http://terminology.hl7.org/CodeSystem/condition-clinical",
                    ["code"] = "active",
                } }
            },
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        });
    }

    private static Built? BuildMedication(XElement sa, string patientUrl)
    {
        var codeEl = sa.Descendants(V3 + "manufacturedMaterial").Elements(V3 + "code").FirstOrDefault();
        var code = Coding(codeEl, Config.SysRxNorm);
        if (code == null) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "MedicationRequest",
            ["status"] = "active",
            ["intent"] = "order",
            ["medicationCodeableConcept"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        });
    }

    private static Built? BuildAllergy(XElement obs, string patientUrl)
    {
        var partCode = obs.Descendants(V3 + "participant").Descendants(V3 + "code").FirstOrDefault();
        var code = Coding(partCode) ?? Coding(obs.Element(V3 + "value"));
        if (code == null) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "AllergyIntolerance",
            ["clinicalStatus"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                {
                    ["system"] = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical",
                    ["code"] = "active",
                } }
            },
            ["code"] = code,
            ["patient"] = new JsonObject { ["reference"] = patientUrl },
        });
    }

    private static Built BuildServiceRequest(string referralId, string patientUrl,
        string? requesterUrl, List<JsonObject> reasonCodes, List<JsonObject> supportingRefs)
    {
        var sr = new JsonObject
        {
            ["resourceType"] = "ServiceRequest",
            ["identifier"] = new JsonArray { new JsonObject
            { ["system"] = "urn:ohia:referral-id", ["value"] = referralId } },
            ["status"] = "active",
            ["intent"] = "order",
            ["code"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = Config.SysSnomed, ["code"] = "306206005", ["display"] = "Referral to service" } },
                ["text"] = "Dental referral",
            },
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
        if (requesterUrl != null) sr["requester"] = new JsonObject { ["reference"] = requesterUrl };
        if (reasonCodes.Count > 0)
        {
            var arr = new JsonArray();
            foreach (var c in reasonCodes) arr.Add(c);
            sr["reasonCode"] = arr;
        }
        if (supportingRefs.Count > 0)
        {
            var arr = new JsonArray();
            foreach (var r in supportingRefs) arr.Add(r);
            sr["reasonReference"] = arr;
        }
        return new Built(Urn(), sr);
    }

    private static Built BuildTask(string referralId, string patientUrl, string serviceRequestUrl) =>
        new(Urn(), new JsonObject
        {
            ["resourceType"] = "Task",
            ["identifier"] = new JsonArray { new JsonObject
            { ["system"] = "urn:ohia:referral-id", ["value"] = referralId } },
            ["status"] = "requested",
            ["intent"] = "order",
            ["code"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://hl7.org/fhir/CodeSystem/task-code", ["code"] = "fulfill" } }
            },
            ["focus"] = new JsonObject { ["reference"] = serviceRequestUrl },
            ["for"] = new JsonObject { ["reference"] = patientUrl },
        });

    private static Built BuildProvenance(IEnumerable<string> targetUrls)
    {
        var targets = new JsonArray();
        foreach (var u in targetUrls) targets.Add(new JsonObject { ["reference"] = u });
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "Provenance",
            ["target"] = targets,
            ["recorded"] = "1970-01-01T00:00:00Z", // set at runtime in the engine
            ["activity"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                {
                    ["system"] = "http://terminology.hl7.org/CodeSystem/v3-DataOperation",
                    ["code"] = "CREATE",
                } }
            },
            ["agent"] = new JsonArray { new JsonObject
            {
                ["who"] = new JsonObject { ["display"] = Config.Settings.AdapterId },
                ["type"] = new JsonObject
                {
                    ["coding"] = new JsonArray { new JsonObject
                    {
                        ["system"] = "http://terminology.hl7.org/CodeSystem/provenance-participant-type",
                        ["code"] = "assembler",
                    } }
                },
            } },
        });
    }
}
