using System.Text;
using System.Text.Json.Nodes;
using System.Xml.Linq;

namespace Ode.Adapter;

/// <summary>Layer 3 (inbound) — C-CDA Referral Note -> FHIR transaction Bundle, and the
/// mirror Consultation Note (PCC-57/59) -> outcome Bundle. Faithful port of ccda_to_fhir.py.
/// Resources are System.Text.Json JsonObjects.</summary>
public static class CcdaToFhir
{
    private static readonly XNamespace V3 = "urn:hl7-org:v3";
    private static readonly IReadOnlySet<string> PriorityWords =
        new HashSet<string> { "routine", "urgent", "asap", "stat" };

    private const string UsCorePatient = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient";
    private const string UsCorePractitioner = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner";
    private const string UsCoreOrganization = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization";

    private sealed record Built(string FullUrl, JsonObject Resource);

    private static string Urn() => $"urn:uuid:{Guid.NewGuid()}";

    private static string Text(XElement? el) => el?.Value.Trim() ?? "";

    private static JsonObject? Coding(XElement? codeEl, string systemDefault = Config.SysSnomed)
    {
        if (codeEl == null) return null;
        var code = codeEl.Attribute("code")?.Value;
        if (string.IsNullOrEmpty(code)) return null;
        var oid = codeEl.Attribute("codeSystem")?.Value;
        var system = oid != null && Config.OidToSystem.TryGetValue(oid, out var s) ? s : systemDefault;
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

    public static JsonObject TransformReferralNote(string cdaXml, string referralId,
                                                   string? direction = null)
    {
        var root = XDocument.Parse(cdaXml).Root!;
        direction = (direction ?? Config.DefaultDirection).ToLowerInvariant();

        var pr = root.Descendants(V3 + "recordTarget").Elements(V3 + "patientRole").FirstOrDefault();
        var patient = BuildPatient(pr);

        var entries = new JsonArray { Entry("POST", "Patient", patient) };

        // Referring provider (author) and rendering provider (recipient).
        var refRoleUrl = AddProvider(entries,
            root.Descendants(V3 + "author").Elements(V3 + "assignedAuthor").FirstOrDefault(), author: true);
        var rndRoleUrl = AddProvider(entries,
            root.Descendants(V3 + "informationRecipient").Elements(V3 + "intendedRecipient").FirstOrDefault(),
            author: false);

        var supportingRefs = new JsonArray();      // reasonReference (Conditions)
        var supportInfoRefs = new JsonArray();      // supportingInfo (ODEMedicationList)
        var medUrls = new List<string>();
        var reasonCodes = new JsonArray();
        JsonObject? serviceCode = null;
        var priority = "routine";
        string? supportingNote = null;
        string? coverageUrl = null;

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
                        if (mr != null)
                        {
                            entries.Add(Entry("POST", "MedicationRequest", mr));
                            medUrls.Add(mr.FullUrl);
                        }
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
                    var stripped = StripPriority(narrative);
                    reasonCodes.Add(new JsonObject
                    { ["text"] = stripped.Length > 500 ? stripped[..500] : stripped });
                    priority = ParsePriority(narrative) ?? priority;
                    break;
                case "plan_of_treatment":
                    var svc = Coding(section.Descendants(V3 + "procedure").Elements(V3 + "code").FirstOrDefault(),
                                     Config.SysCpt);
                    if (svc != null) serviceCode = svc;
                    break;
                case "payers":
                    var (cov, payerOrg) = BuildCoverage(narrative, patient.FullUrl);
                    if (cov != null)
                    {
                        if (payerOrg != null) entries.Add(Entry("POST", "Organization", payerOrg));
                        entries.Add(Entry("POST", "Coverage", cov));
                        coverageUrl = cov.FullUrl;
                    }
                    break;
                case "clinical_info":
                    supportingNote = string.IsNullOrEmpty(narrative) ? null : narrative;
                    break;
            }
        }

        // ODEMedicationList (List) aggregating the current medications.
        if (medUrls.Count > 0)
        {
            var medList = Cow.BuildMedicationList(referralId, patient.FullUrl, medUrls);
            var medListUrl = JsonX.Str(medList["_fullUrl"])!;
            medList.Remove("_fullUrl");
            entries.Add(Entry("POST", "List", new Built(medListUrl, medList)));
            supportInfoRefs.Add(new JsonObject { ["reference"] = medListUrl });
        }

        var serviceRequest = BuildServiceRequest(referralId, direction, patient.FullUrl, refRoleUrl,
            rndRoleUrl, reasonCodes, supportingRefs, serviceCode, priority, coverageUrl,
            supportingNote, supportInfoRefs);
        entries.Add(Entry("POST", "ServiceRequest", serviceRequest));

        var task = BuildTask(referralId, patient.FullUrl, serviceRequest.FullUrl);
        entries.Add(Entry("POST", "Task", task));

        var prov = BuildProvenance(new[] { serviceRequest.FullUrl, task.FullUrl });
        entries.Add(Entry("POST", "Provenance", prov));

        return new JsonObject { ["resourceType"] = "Bundle", ["type"] = "transaction", ["entry"] = entries };
    }

    /// <summary>Parse a 360X Consultation Note C-CDA (PCC-57 outcome or PCC-59 interim) into a
    /// FHIR transaction Bundle of outcome resources. Mirror of fhir_to_ccda.BuildConsultationNote.</summary>
    public static JsonObject TransformConsultationNote(string cdaXml, string referralId, bool interim = false)
    {
        var root = XDocument.Parse(cdaXml).Root!;
        var pr = root.Descendants(V3 + "recordTarget").Elements(V3 + "patientRole").FirstOrDefault();
        var patient = BuildPatient(pr);

        var entries = new JsonArray { Entry("POST", "Patient", patient) };
        var outcomeUrls = new List<string>();

        var structuredBody = root.Descendants(V3 + "structuredBody").FirstOrDefault();
        var sections = structuredBody?.Elements(V3 + "component").Elements(V3 + "section")
                       ?? Enumerable.Empty<XElement>();

        foreach (var section in sections)
        {
            var loinc = section.Element(V3 + "code")?.Attribute("code")?.Value;
            var narrative = Text(section.Element(V3 + "text"));
            switch (loinc)
            {
                case "51847-2":   // Assessment and Plan
                    var ci = BuildClinicalImpression(narrative, patient.FullUrl);
                    entries.Add(Entry("POST", "ClinicalImpression", ci));
                    outcomeUrls.Add(ci.FullUrl);
                    var cp = BuildCarePlan(narrative, patient.FullUrl);
                    if (cp != null) { entries.Add(Entry("POST", "CarePlan", cp)); outcomeUrls.Add(cp.FullUrl); }
                    break;
                case "47519-4":   // Procedures (structured, if present)
                    foreach (var proc in section.Descendants(V3 + "procedure"))
                    {
                        var p = BuildProcedure(proc, patient.FullUrl);
                        if (p != null) { entries.Add(Entry("POST", "Procedure", p)); outcomeUrls.Add(p.FullUrl); }
                    }
                    break;
                case "30954-2":   // Results (structured, if present)
                    foreach (var obs in section.Descendants(V3 + "observation"))
                    {
                        var o = BuildResultObservation(obs, patient.FullUrl);
                        if (o != null) { entries.Add(Entry("POST", "Observation", o)); outcomeUrls.Add(o.FullUrl); }
                    }
                    break;
            }
        }

        var docref = BuildDocumentReference(cdaXml, patient.FullUrl, referralId, interim);
        entries.Add(Entry("POST", "DocumentReference", docref));
        outcomeUrls.Add(docref.FullUrl);

        var prov = BuildProvenance(outcomeUrls);
        entries.Add(Entry("POST", "Provenance", prov));

        return new JsonObject { ["resourceType"] = "Bundle", ["type"] = "transaction", ["entry"] = entries };
    }

    // ----------------------------- builders --------------------------------- //
    private static Built BuildPatient(XElement? pr)
    {
        var res = new JsonObject
        {
            ["resourceType"] = "Patient",
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCorePatient } },
        };
        if (pr != null)
        {
            var idel = pr.Element(V3 + "id");
            var ext = idel?.Attribute("extension")?.Value;
            if (!string.IsNullOrEmpty(ext))
                res["identifier"] = new JsonArray { new JsonObject
                { ["system"] = idel!.Attribute("root")?.Value ?? "urn:oid:unknown", ["value"] = ext } };
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
                { ["family"] = Text(name.Element(V3 + "family")), ["given"] = given } };
            }
            var gcode = pr.Element(V3 + "patient")?.Element(V3 + "administrativeGenderCode")?.Attribute("code")?.Value;
            if (!string.IsNullOrEmpty(gcode))
                res["gender"] = gcode == "M" ? "male" : gcode == "F" ? "female" : "unknown";
            var bd = pr.Element(V3 + "patient")?.Element(V3 + "birthTime")?.Attribute("value")?.Value;
            if (!string.IsNullOrEmpty(bd) && bd.Length >= 8)
                res["birthDate"] = $"{bd[..4]}-{bd.Substring(4, 2)}-{bd.Substring(6, 2)}";
            var tel = pr.Element(V3 + "telecom")?.Attribute("value")?.Value;
            if (!string.IsNullOrEmpty(tel))
                res["telecom"] = new JsonArray { new JsonObject
                { ["system"] = "phone", ["value"] = tel.Replace("tel:", "") } };
            var addr = pr.Element(V3 + "addr");
            if (addr != null)
            {
                var a = new JsonObject();
                var line = Text(addr.Element(V3 + "streetAddressLine"));
                if (line.Length > 0) a["line"] = new JsonArray { line };
                foreach (var (tag, key) in new[] { ("city", "city"), ("state", "state"), ("postalCode", "postalCode") })
                {
                    var v = Text(addr.Element(V3 + tag));
                    if (v.Length > 0) a[key] = v;
                }
                if (a.Count > 0) res["address"] = new JsonArray { a };
            }
        }
        return new Built(Urn(), res);
    }

    private static string? AddProvider(JsonArray entries, XElement? el, bool author)
    {
        var (pract, org, role) = BuildProviderRole(el, author);
        if (pract != null) entries.Add(Entry("POST", "Practitioner", pract));
        if (org != null) entries.Add(Entry("POST", "Organization", org));
        if (role != null) entries.Add(Entry("POST", "PractitionerRole", role));
        return role?.FullUrl;
    }

    private static (Built? pract, Built? org, Built? role) BuildProviderRole(XElement? el, bool author)
    {
        if (el == null) return (null, null, null);
        var practitioner = new JsonObject
        {
            ["resourceType"] = "Practitioner",
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCorePractitioner } },
        };
        var ext = el.Element(V3 + "id")?.Attribute("extension")?.Value;
        if (!string.IsNullOrEmpty(ext))
            practitioner["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysNpi, ["value"] = ext } };
        var nameEl = author
            ? el.Element(V3 + "assignedPerson")?.Element(V3 + "name")
            : el.Element(V3 + "informationRecipient")?.Element(V3 + "name");
        if (nameEl != null)
        {
            var family = Text(nameEl.Element(V3 + "family"));
            var given = new JsonArray();
            foreach (var g in nameEl.Elements(V3 + "given"))
            {
                var t = g.Value.Trim();
                if (t.Length > 0) given.Add(t);
            }
            if (!string.IsNullOrEmpty(family) || given.Count > 0)
                practitioner["name"] = new JsonArray { new JsonObject { ["family"] = family, ["given"] = given } };
            else if (Text(nameEl).Length > 0)
                practitioner["name"] = new JsonArray { new JsonObject { ["text"] = Text(nameEl) } };
        }
        var practBuilt = new Built(Urn(), practitioner);

        Built? orgBuilt = null;
        var orgEl = author ? el.Element(V3 + "representedOrganization") : el.Element(V3 + "receivedOrganization");
        var orgName = orgEl != null ? Text(orgEl.Element(V3 + "name")) : "";
        if (orgEl != null && orgName.Length > 0)
            orgBuilt = new Built(Urn(), new JsonObject
            {
                ["resourceType"] = "Organization",
                ["_fullUrl"] = "",   // placeholder; not serialized (Built carries the url)
                ["name"] = orgName,
                ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCoreOrganization } },
            });
        if (orgBuilt != null) orgBuilt.Resource.Remove("_fullUrl");

        var role = new JsonObject
        {
            ["resourceType"] = "PractitionerRole",
            ["practitioner"] = new JsonObject { ["reference"] = practBuilt.FullUrl },
        };
        if (orgBuilt != null) role["organization"] = new JsonObject { ["reference"] = orgBuilt.FullUrl };
        var spec = el.Element(V3 + "code")?.Attribute("displayName")?.Value;
        if (!string.IsNullOrEmpty(spec))
            role["specialty"] = new JsonArray { new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject { ["system"] = Config.SysNucc, ["display"] = spec } },
                ["text"] = spec,
            } };
        return (practBuilt, orgBuilt, new Built(Urn(), role));
    }

    private static Built? BuildCondition(XElement obs, string patientUrl)
    {
        var code = Coding(obs.Element(V3 + "value"));
        if (code == null) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "Condition",
            ["clinicalStatus"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/condition-clinical", ["code"] = "active" } } },
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
            ["clinicalStatus"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/allergyintolerance-clinical", ["code"] = "active" } } },
            ["code"] = code,
            ["patient"] = new JsonObject { ["reference"] = patientUrl },
        });
    }

    private static (Built? cov, Built? payerOrg) BuildCoverage(string narrative, string patientUrl)
    {
        var fields = new Dictionary<string, string>();
        foreach (var line in narrative.Replace(";", "\n").Split('\n'))
        {
            var idx = line.IndexOf(':');
            if (idx < 0) continue;
            fields[line[..idx].Trim().ToLowerInvariant()] = line[(idx + 1)..].Trim();
        }
        fields.TryGetValue("payer", out var payer);
        var member = fields.TryGetValue("member id", out var m1) ? m1
            : (fields.TryGetValue("member", out var m2) ? m2 : null);
        if (string.IsNullOrEmpty(payer) && string.IsNullOrEmpty(member)) return (null, null);

        Built? payerOrg = null;
        var payor = new JsonArray();
        if (!string.IsNullOrEmpty(payer))
        {
            payerOrg = new Built(Urn(), new JsonObject
            {
                ["resourceType"] = "Organization",
                ["type"] = new JsonArray { new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/organization-type", ["code"] = "pay", ["display"] = "Payer" } } } },
                ["name"] = payer,
            });
            payor.Add(new JsonObject { ["reference"] = payerOrg.FullUrl });
        }
        else payor.Add(new JsonObject { ["display"] = "Unknown Payer" });

        var cov = new JsonObject
        {
            ["resourceType"] = "Coverage",
            ["status"] = "active",
            ["beneficiary"] = new JsonObject { ["reference"] = patientUrl },
            ["payor"] = payor,
        };
        if (!string.IsNullOrEmpty(member))
        {
            cov["subscriberId"] = member;
            cov["identifier"] = new JsonArray { new JsonObject
            { ["system"] = "http://hospital.example.org/member-id", ["value"] = member } };
        }
        if (fields.TryGetValue("relationship", out var rel) && !string.IsNullOrEmpty(rel))
            cov["relationship"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/subscriber-relationship", ["code"] = rel } },
                ["text"] = rel,
            };
        var klass = new JsonArray();
        if (fields.TryGetValue("group", out var group) && !string.IsNullOrEmpty(group))
            klass.Add(new JsonObject
            {
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/coverage-class", ["code"] = "group" } } },
                ["value"] = group,
            });
        if (fields.TryGetValue("plan", out var plan) && !string.IsNullOrEmpty(plan))
            klass.Add(new JsonObject
            {
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/coverage-class", ["code"] = "plan" } } },
                ["value"] = plan,
            });
        if (klass.Count > 0) cov["class"] = klass;
        return (new Built(Urn(), cov), payerOrg);
    }

    private static string? ParsePriority(string narrative)
    {
        var low = (narrative ?? "").ToLowerInvariant();
        var idx = low.IndexOf("priority:");
        if (idx < 0) return null;
        var after = low[(idx + "priority:".Length)..].TrimStart();
        foreach (var w in PriorityWords)
            if (after.StartsWith(w)) return w;
        return null;
    }

    private static string StripPriority(string narrative)
    {
        var parts = (narrative ?? "").Split('\n')
            .Select(p => p.Trim()).Where(p => p.Length > 0)
            .Where(p => !p.ToLowerInvariant().StartsWith("priority:")).ToList();
        var joined = string.Join(" ", parts).Trim();
        return joined.Length > 0 ? joined : (narrative ?? "").Trim();
    }

    private static Built BuildServiceRequest(string referralId, string direction, string patientUrl,
        string? requesterUrl, string? performerUrl, JsonArray reasonCodes, JsonArray supportingRefs,
        JsonObject? serviceCode, string priority, string? coverageUrl, string? note, JsonArray supportInfoRefs)
    {
        var code = serviceCode ?? new JsonObject
        {
            ["coding"] = new JsonArray { new JsonObject
            { ["system"] = Config.SysSnomed, ["code"] = "306206005", ["display"] = "Referral to service" } },
            ["text"] = "Referral",
        };
        if (Config.MedicalSideDirections.Contains(direction))
        {
            var codings = new JsonArray();
            foreach (var c in JsonX.Arr(code["coding"]) ?? new JsonArray())
                if (JsonX.Str((c as JsonObject)?["system"]) != Config.SysCdt) codings.Add(c!.DeepClone());
            var trimmed = new JsonObject();
            foreach (var kv in code) if (kv.Key != "coding") trimmed[kv.Key] = kv.Value?.DeepClone();
            if (codings.Count > 0) trimmed["coding"] = codings;
            code = trimmed;
        }
        var profile = Config.ReferralProfileByDirection.TryGetValue(direction, out var p)
            ? p : Config.ProfileOdeMedToDental;
        var sr = new JsonObject
        {
            ["resourceType"] = "ServiceRequest",
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { profile } },
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "active",
            ["intent"] = "order",
            ["priority"] = PriorityWords.Contains(priority) ? priority : "routine",
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
        if (requesterUrl != null) sr["requester"] = new JsonObject { ["reference"] = requesterUrl };
        if (performerUrl != null) sr["performer"] = new JsonArray { new JsonObject { ["reference"] = performerUrl } };
        if (reasonCodes.Count > 0) sr["reasonCode"] = reasonCodes;
        if (supportingRefs.Count > 0) sr["reasonReference"] = supportingRefs;
        if (supportInfoRefs.Count > 0) sr["supportingInfo"] = supportInfoRefs;
        if (coverageUrl != null) sr["insurance"] = new JsonArray { new JsonObject { ["reference"] = coverageUrl } };
        if (!string.IsNullOrEmpty(note)) sr["note"] = new JsonArray { new JsonObject { ["text"] = note } };
        return new Built(Urn(), sr);
    }

    private static Built BuildTask(string referralId, string patientUrl, string serviceRequestUrl) =>
        new(Urn(), new JsonObject
        {
            ["resourceType"] = "Task",
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { Config.ProfileOdeReferralTask } },
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "requested",
            ["intent"] = "order",
            ["businessStatus"] = Cow.BusinessStatusConcept("received"),
            ["code"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://hl7.org/fhir/CodeSystem/task-code", ["code"] = "fulfill" } } },
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
            ["activity"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/v3-DataOperation", ["code"] = "CREATE" } } },
            ["agent"] = new JsonArray { new JsonObject
            {
                ["who"] = new JsonObject { ["display"] = Config.Settings.AdapterId },
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/provenance-participant-type", ["code"] = "assembler" } } },
            } },
        });
    }

    // -------------------- consultation-note (reply) builders ---------------- //
    private static Built BuildClinicalImpression(string narrative, string patientUrl)
    {
        var summary = AssessmentText(narrative);
        if (string.IsNullOrEmpty(summary)) summary = string.IsNullOrEmpty(narrative) ? "Referral outcome." : narrative;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "ClinicalImpression",
            ["status"] = "completed",
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
            ["summary"] = summary.Length > 1000 ? summary[..1000] : summary,
        });
    }

    private static Built? BuildCarePlan(string narrative, string patientUrl)
    {
        var plan = PlanText(narrative);
        if (string.IsNullOrEmpty(plan)) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "CarePlan",
            ["status"] = "active",
            ["intent"] = "plan",
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
            ["description"] = plan.Length > 1000 ? plan[..1000] : plan,
        });
    }

    private static Built? BuildProcedure(XElement proc, string patientUrl)
    {
        var code = Coding(proc.Element(V3 + "code"));
        if (code == null) return null;
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "Procedure",
            ["status"] = "completed",
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        });
    }

    private static Built? BuildResultObservation(XElement obs, string patientUrl)
    {
        var code = Coding(obs.Element(V3 + "code"));
        if (code == null) return null;
        var res = new JsonObject
        {
            ["resourceType"] = "Observation",
            ["status"] = "final",
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
        var val = obs.Element(V3 + "value");
        if (val != null)
        {
            var v = val.Attribute("value")?.Value;
            if (!string.IsNullOrEmpty(v))
                res["valueQuantity"] = new JsonObject { ["value"] = Num(v), ["unit"] = val.Attribute("unit")?.Value ?? "" };
            else if (Text(val).Length > 0)
                res["valueString"] = Text(val);
        }
        return new Built(Urn(), res);
    }

    private static Built BuildDocumentReference(string cdaXml, string patientUrl, string referralId, bool interim)
    {
        var data = Convert.ToBase64String(Encoding.UTF8.GetBytes(cdaXml));
        return new Built(Urn(), new JsonObject
        {
            ["resourceType"] = "DocumentReference",
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "current",
            ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = Config.SysLoinc, ["code"] = Config.DocConsultationNote, ["display"] = "Consultation Note" } } },
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
            ["description"] = interim ? "Interim consultation note" : "Referral outcome consultation note",
            ["content"] = new JsonArray { new JsonObject { ["attachment"] = new JsonObject
            { ["contentType"] = "application/xml", ["data"] = data } } },
        });
    }

    private static string AssessmentText(string narrative)
    {
        foreach (var line in (narrative ?? "").Replace(".", ".\n").Split('\n'))
            if (line.ToLowerInvariant().Contains("assessment")) return line.Trim();
        return (narrative ?? "").Trim();
    }

    private static string PlanText(string narrative)
    {
        foreach (var line in (narrative ?? "").Replace(".", ".\n").Split('\n'))
            if (line.ToLowerInvariant().TrimStart().StartsWith("plan"))
            {
                var idx = line.IndexOf(':');
                return (idx >= 0 ? line[(idx + 1)..] : line).Trim();
            }
        return "";
    }

    private static JsonNode Num(string value)
    {
        if (value.Contains('.') && double.TryParse(value, out var d)) return JsonValue.Create(d);
        if (long.TryParse(value, out var l)) return JsonValue.Create(l);
        return JsonValue.Create(value);
    }
}
