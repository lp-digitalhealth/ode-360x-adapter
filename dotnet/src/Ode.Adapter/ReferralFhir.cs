using System.Text.Json.Nodes;

namespace Ode.Adapter;

/// <summary>Structured referral -> rich FHIR transaction Bundle. Faithful port of
/// referral_fhir.py. A referral must carry enough for the receiver to diagnose, treat,
/// and bill: Patient, Coverage (+ payer Organization), Practitioner/PractitionerRole/
/// Organization for BOTH referring (requester) and rendering (performer) providers,
/// Condition(s), an ODEMedicationList, a directional ServiceRequest, a COW Task, and a
/// Provenance. Resources embed a `_fullUrl` that is stripped when wrapped into an entry.</summary>
public static class ReferralFhir
{
    private static readonly IReadOnlySet<string> PriorityValues =
        new HashSet<string> { "routine", "urgent", "asap", "stat" };

    private const string UsCorePatient = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-patient";
    private const string UsCorePractitioner = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner";
    private const string UsCoreOrganization = "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization";

    private static string Urn() => $"urn:uuid:{Guid.NewGuid()}";

    private static JsonObject Entry(JsonObject resource, string? rtype = null)
    {
        rtype ??= JsonX.Str(resource["resourceType"]);
        var clean = new JsonObject();
        foreach (var kv in resource)
            if (!kv.Key.StartsWith("_")) clean[kv.Key] = kv.Value?.DeepClone();
        return new JsonObject
        {
            ["fullUrl"] = JsonX.Str(resource["_fullUrl"]),
            ["resource"] = clean,
            ["request"] = new JsonObject { ["method"] = "POST", ["url"] = rtype },
        };
    }

    private static JsonObject? Codeable(string? code, string system, string? display)
    {
        if (string.IsNullOrEmpty(code) && string.IsNullOrEmpty(display)) return null;
        var cc = new JsonObject();
        if (!string.IsNullOrEmpty(code))
            cc["coding"] = new JsonArray { new JsonObject { ["system"] = system, ["code"] = code, ["display"] = display } };
        cc["text"] = display ?? code;
        return cc;
    }

    public static JsonObject BuildReferralBundle(JsonObject rich)
    {
        var referralId = JsonX.Str(rich["referral_id"]) ?? "REF-UNKNOWN";
        var direction = (JsonX.Str(rich["direction"]) ?? Config.DefaultDirection).ToLowerInvariant();
        var entries = new JsonArray();

        var patient = BuildPatient(JsonX.Obj(rich["patient"]) ?? new JsonObject());
        var patientUrl = JsonX.Str(patient["_fullUrl"])!;
        entries.Add(Entry(patient));

        // Referring (requester) and rendering (performer) providers.
        string? refRoleUrl = null;
        var referring = JsonX.Obj(rich["referring_provider"]);
        if (referring != null && referring.Count > 0)
        {
            var (pract, org, role) = BuildProviderRole(referring);
            entries.Add(Entry(pract));
            if (org != null) entries.Add(Entry(org));
            entries.Add(Entry(role));
            refRoleUrl = JsonX.Str(role["_fullUrl"]);
        }

        string? rndRoleUrl = null;
        var rendering = JsonX.Obj(rich["rendering_provider"]);
        if (rendering != null && rendering.Count > 0)
        {
            var (pract, org, role) = BuildProviderRole(rendering);
            entries.Add(Entry(pract));
            if (org != null) entries.Add(Entry(org));
            entries.Add(Entry(role));
            rndRoleUrl = JsonX.Str(role["_fullUrl"]);
        }

        // Coverage (billing).
        string? coverageUrl = null;
        var covIn = JsonX.Obj(rich["coverage"]);
        if (covIn != null && (!string.IsNullOrEmpty(JsonX.Str(covIn["payer"])) ||
                              !string.IsNullOrEmpty(JsonX.Str(covIn["member_id"]))))
        {
            var payerOrg = BuildPayerOrg(covIn);
            entries.Add(Entry(payerOrg));
            var coverage = BuildCoverage(covIn, patientUrl, JsonX.Str(payerOrg["_fullUrl"])!);
            coverageUrl = JsonX.Str(coverage["_fullUrl"]);
            entries.Add(Entry(coverage));
        }

        // Diagnoses.
        var conditionUrls = new List<string>();
        var dxCodes = new List<JsonObject>();
        foreach (var dxNode in JsonX.Arr(rich["diagnoses"]) ?? new JsonArray())
        {
            var dx = dxNode as JsonObject;
            if (dx == null) continue;
            var cond = BuildCondition(dx, patientUrl);
            if (cond == null) continue;
            conditionUrls.Add(JsonX.Str(cond["_fullUrl"])!);
            var code = JsonX.Obj(cond["code"]);
            if (code != null) dxCodes.Add(code);
            entries.Add(Entry(cond));
        }

        // Current medication list (supporting clinical context for the order).
        var medUrls = new List<string>();
        foreach (var medNode in JsonX.Arr(rich["medications"]) ?? new JsonArray())
        {
            var med = medNode as JsonObject;
            if (med == null) continue;
            var mr = BuildMedication(med, patientUrl);
            if (mr == null) continue;
            medUrls.Add(JsonX.Str(mr["_fullUrl"])!);
            entries.Add(Entry(mr));
        }

        // Aggregate meds into the ODEMedicationList (List) the contract expects; the
        // ServiceRequest references the List (not each MedicationRequest) in supportingInfo.
        var supportUrls = new List<string>();
        if (medUrls.Count > 0)
        {
            var medList = Cow.BuildMedicationList(referralId, patientUrl, medUrls);
            entries.Add(Entry(medList));
            supportUrls.Add(JsonX.Str(medList["_fullUrl"])!);
        }

        var serviceRequest = BuildServiceRequest(rich, direction, patientUrl, refRoleUrl, rndRoleUrl,
                                                 conditionUrls, dxCodes, coverageUrl, supportUrls);
        var srUrl = JsonX.Str(serviceRequest["_fullUrl"])!;
        entries.Add(Entry(serviceRequest));

        var task = BuildTask(referralId, patientUrl, srUrl);
        var taskUrl = JsonX.Str(task["_fullUrl"])!;
        entries.Add(Entry(task));

        var prov = BuildProvenance(new[] { srUrl, taskUrl });
        entries.Add(Entry(prov));

        return new JsonObject { ["resourceType"] = "Bundle", ["type"] = "transaction", ["entry"] = entries };
    }

    // --------------------------------------------------------------------- //
    private static JsonObject BuildPatient(JsonObject p)
    {
        var res = new JsonObject
        {
            ["resourceType"] = "Patient",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCorePatient } },
            ["identifier"] = new JsonArray { new JsonObject
            { ["system"] = "http://hospital.example.org/mrn", ["value"] = JsonX.Str(p["mrn"]) ?? "MRN-UNKNOWN" } },
        };
        var given = new JsonArray();
        var g = JsonX.Str(p["given"]);
        if (!string.IsNullOrEmpty(g)) given.Add(g);
        res["name"] = new JsonArray { new JsonObject { ["family"] = JsonX.Str(p["family"]) ?? "", ["given"] = given } };
        var gender = JsonX.Str(p["gender"]);
        if (!string.IsNullOrEmpty(gender)) res["gender"] = gender;
        var bd = JsonX.Str(p["birthDate"]);
        if (!string.IsNullOrEmpty(bd)) res["birthDate"] = bd;
        var phone = JsonX.Str(p["phone"]);
        if (!string.IsNullOrEmpty(phone))
            res["telecom"] = new JsonArray { new JsonObject { ["system"] = "phone", ["value"] = phone, ["use"] = "home" } };
        var addr = JsonX.Obj(p["address"]);
        if (addr != null && new[] { "line", "city", "state", "postalCode" }
                .Any(k => !string.IsNullOrEmpty(JsonX.Str(addr[k]))))
        {
            var a = new JsonObject();
            var line = JsonX.Str(addr["line"]);
            if (!string.IsNullOrEmpty(line)) a["line"] = new JsonArray { line };
            foreach (var k in new[] { "city", "state", "postalCode" })
            {
                var v = JsonX.Str(addr[k]);
                if (!string.IsNullOrEmpty(v)) a[k] = v;
            }
            res["address"] = new JsonArray { a };
        }
        return res;
    }

    private static (JsonObject pract, JsonObject? org, JsonObject role) BuildProviderRole(JsonObject pr)
    {
        var pract = new JsonObject
        {
            ["resourceType"] = "Practitioner",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCorePractitioner } },
            ["name"] = new JsonArray { new JsonObject { ["text"] = JsonX.Str(pr["name"]) ?? "Unknown Provider" } },
        };
        var npi = JsonX.Str(pr["npi"]);
        if (!string.IsNullOrEmpty(npi))
            pract["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysNpi, ["value"] = npi } };

        JsonObject? org = null;
        var orgName = JsonX.Str(pr["organization"]);
        if (!string.IsNullOrEmpty(orgName))
            org = new JsonObject
            {
                ["resourceType"] = "Organization",
                ["_fullUrl"] = Urn(),
                ["name"] = orgName,
                ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCoreOrganization } },
            };

        var role = new JsonObject
        {
            ["resourceType"] = "PractitionerRole",
            ["_fullUrl"] = Urn(),
            ["practitioner"] = new JsonObject { ["reference"] = JsonX.Str(pract["_fullUrl"]) },
        };
        if (org != null) role["organization"] = new JsonObject { ["reference"] = JsonX.Str(org["_fullUrl"]) };
        var spec = JsonX.Str(pr["specialty"]);
        if (!string.IsNullOrEmpty(spec))
            role["specialty"] = new JsonArray { new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject { ["system"] = Config.SysNucc, ["display"] = spec } },
                ["text"] = spec,
            } };
        var phone = JsonX.Str(pr["phone"]);
        if (!string.IsNullOrEmpty(phone))
            role["telecom"] = new JsonArray { new JsonObject { ["system"] = "phone", ["value"] = phone } };
        return (pract, org, role);
    }

    private static JsonObject BuildPayerOrg(JsonObject cov) => new()
    {
        ["resourceType"] = "Organization",
        ["_fullUrl"] = Urn(),
        ["type"] = new JsonArray { new JsonObject
        {
            ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/organization-type", ["code"] = "pay", ["display"] = "Payer" } }
        } },
        ["name"] = JsonX.Str(cov["payer"]) ?? "Unknown Payer",
    };

    private static JsonObject BuildCoverage(JsonObject cov, string patientUrl, string payerUrl)
    {
        var res = new JsonObject
        {
            ["resourceType"] = "Coverage",
            ["_fullUrl"] = Urn(),
            ["status"] = "active",
            ["beneficiary"] = new JsonObject { ["reference"] = patientUrl },
            ["payor"] = new JsonArray { new JsonObject { ["reference"] = payerUrl } },
        };
        var member = JsonX.Str(cov["member_id"]);
        if (!string.IsNullOrEmpty(member))
        {
            res["subscriberId"] = member;
            res["identifier"] = new JsonArray { new JsonObject
            { ["system"] = "http://hospital.example.org/member-id", ["value"] = member } };
        }
        var rel = JsonX.Str(cov["relationship"]);
        if (!string.IsNullOrEmpty(rel))
            res["relationship"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/subscriber-relationship", ["code"] = rel } },
                ["text"] = rel,
            };
        var klass = new JsonArray();
        var group = JsonX.Str(cov["group"]);
        if (!string.IsNullOrEmpty(group))
            klass.Add(new JsonObject
            {
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/coverage-class", ["code"] = "group" } } },
                ["value"] = group,
            });
        var plan = JsonX.Str(cov["plan"]);
        if (!string.IsNullOrEmpty(plan))
            klass.Add(new JsonObject
            {
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/coverage-class", ["code"] = "plan" } } },
                ["value"] = plan,
            });
        if (klass.Count > 0) res["class"] = klass;
        return res;
    }

    private static string DxSystem(JsonObject dx)
    {
        var sys = (JsonX.Str(dx["system"]) ?? "icd10").ToLowerInvariant();
        return sys switch
        {
            "icd10" or "icd-10" => Config.SysIcd10,
            "snomed" => Config.SysSnomed,
            "cdt" => Config.SysCdt,
            _ => Config.SysIcd10,
        };
    }

    private static JsonObject? BuildCondition(JsonObject dx, string patientUrl)
    {
        var code = Codeable(JsonX.Str(dx["code"]), DxSystem(dx), JsonX.Str(dx["display"]));
        if (code == null) return null;
        return new JsonObject
        {
            ["resourceType"] = "Condition",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray
            { "http://hl7.org/fhir/us/core/StructureDefinition/us-core-condition-problems-health-concerns" } },
            ["clinicalStatus"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/condition-clinical", ["code"] = "active" } } },
            ["category"] = new JsonArray { new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/condition-category", ["code"] = "encounter-diagnosis" } } } },
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
    }

    private static JsonObject? BuildMedication(JsonObject med, string patientUrl)
    {
        var code = Codeable(JsonX.Str(med["code"]), Config.SysRxNorm, JsonX.Str(med["display"]));
        if (code == null) return null;
        return new JsonObject
        {
            ["resourceType"] = "MedicationRequest",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray
            { "http://hl7.org/fhir/us/core/StructureDefinition/us-core-medicationrequest" } },
            ["status"] = "active",
            ["intent"] = "order",
            ["medicationCodeableConcept"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
    }

    private static string ServiceSystem(JsonObject svc)
    {
        var sys = (JsonX.Str(svc["system"]) ?? "cpt").ToLowerInvariant();
        return sys switch
        {
            "cpt" => Config.SysCpt,
            "hcpcs" => Config.SysHcpcs,
            "loinc" => Config.SysLoinc,
            "cdt" => Config.SysCdt,
            "snomed" => Config.SysSnomed,
            _ => Config.SysCpt,
        };
    }

    private static JsonObject DropCdtCoding(JsonObject code)
    {
        var codings = new JsonArray();
        foreach (var c in JsonX.Arr(code["coding"]) ?? new JsonArray())
            if (JsonX.Str((c as JsonObject)?["system"]) != Config.SysCdt) codings.Add(c!.DeepClone());
        var outCc = new JsonObject();
        foreach (var kv in code) if (kv.Key != "coding") outCc[kv.Key] = kv.Value?.DeepClone();
        if (codings.Count > 0) outCc["coding"] = codings;
        return outCc;
    }

    private static JsonObject BuildServiceRequest(JsonObject rich, string direction, string patientUrl,
        string? requesterUrl, string? performerUrl, List<string> conditionUrls, List<JsonObject> dxCodes,
        string? coverageUrl, List<string> supportUrls)
    {
        var svc = JsonX.Obj(rich["service"]) ?? new JsonObject();
        var code = Codeable(JsonX.Str(svc["code"]), ServiceSystem(svc), JsonX.Str(svc["display"]))
            ?? new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = Config.SysSnomed, ["code"] = "306206005", ["display"] = "Referral to service" } },
                ["text"] = "Referral",
            };
        // Must-support: medical-side receivers do not carry CDT (keep the text).
        if (Config.MedicalSideDirections.Contains(direction)) code = DropCdtCoding(code);

        var priority = (JsonX.Str(rich["priority"]) ?? "routine").ToLowerInvariant();
        if (!PriorityValues.Contains(priority)) priority = "routine";

        var reasonCodes = new JsonArray();
        var reasonText = JsonX.Str(rich["reason_text"]);
        if (!string.IsNullOrEmpty(reasonText)) reasonCodes.Add(new JsonObject { ["text"] = reasonText });
        foreach (var c in dxCodes) reasonCodes.Add(JsonX.Clone(c));

        var profile = Config.ReferralProfileByDirection.TryGetValue(direction, out var p)
            ? p : Config.ProfileOdeMedToDental;

        var sr = new JsonObject
        {
            ["resourceType"] = "ServiceRequest",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { profile } },
            ["identifier"] = new JsonArray { new JsonObject
            { ["system"] = Config.SysReferralId, ["value"] = JsonX.Str(rich["referral_id"]) } },
            ["status"] = "active",
            ["intent"] = "order",
            ["priority"] = priority,
            ["code"] = code,
            ["subject"] = new JsonObject { ["reference"] = patientUrl },
        };
        if (reasonCodes.Count > 0) sr["reasonCode"] = reasonCodes;
        if (conditionUrls.Count > 0)
        {
            var arr = new JsonArray();
            foreach (var u in conditionUrls) arr.Add(new JsonObject { ["reference"] = u });
            sr["reasonReference"] = arr;
        }
        if (requesterUrl != null) sr["requester"] = new JsonObject { ["reference"] = requesterUrl };
        if (performerUrl != null) sr["performer"] = new JsonArray { new JsonObject { ["reference"] = performerUrl } };
        if (coverageUrl != null) sr["insurance"] = new JsonArray { new JsonObject { ["reference"] = coverageUrl } };
        if (supportUrls.Count > 0)
        {
            var arr = new JsonArray();
            foreach (var u in supportUrls) arr.Add(new JsonObject { ["reference"] = u });
            sr["supportingInfo"] = arr;
        }
        var support = JsonX.Str(rich["supporting_info"]);
        if (!string.IsNullOrEmpty(support)) sr["note"] = new JsonArray { new JsonObject { ["text"] = support } };
        return sr;
    }

    private static JsonObject BuildTask(string referralId, string patientUrl, string serviceRequestUrl) => new()
    {
        ["resourceType"] = "Task",
        ["_fullUrl"] = Urn(),
        ["meta"] = new JsonObject { ["profile"] = new JsonArray { Config.ProfileOdeReferralTask } },
        ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
        ["status"] = "requested",
        ["intent"] = "order",
        ["businessStatus"] = Cow.BusinessStatusConcept("received"),
        ["code"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
        { ["system"] = "http://hl7.org/fhir/CodeSystem/task-code", ["code"] = "fulfill" } } },
        ["focus"] = new JsonObject { ["reference"] = serviceRequestUrl },
        ["for"] = new JsonObject { ["reference"] = patientUrl },
    };

    private static JsonObject BuildProvenance(IEnumerable<string> targetUrls)
    {
        var targets = new JsonArray();
        foreach (var u in targetUrls) targets.Add(new JsonObject { ["reference"] = u });
        return new JsonObject
        {
            ["resourceType"] = "Provenance",
            ["_fullUrl"] = Urn(),
            ["target"] = targets,
            ["recorded"] = "1970-01-01T00:00:00Z",   // stamped at runtime in the engine
            ["activity"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
            { ["system"] = "http://terminology.hl7.org/CodeSystem/v3-DataOperation", ["code"] = "CREATE" } } },
            ["agent"] = new JsonArray { new JsonObject
            {
                ["who"] = new JsonObject { ["display"] = Config.Settings.AdapterId },
                ["type"] = new JsonObject { ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://terminology.hl7.org/CodeSystem/provenance-participant-type", ["code"] = "assembler" } } },
            } },
        };
    }
}
