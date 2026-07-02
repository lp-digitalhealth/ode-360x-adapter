using System.Text.Json.Nodes;

namespace Ode.Adapter;

/// <summary>COW subset helpers — the scoped Clinical Order Workflows layer.
/// Faithful port of cow.py. Builds the COW/FHIR artifacts the bridge applies on every
/// reply: the dental businessStatus, Task.output wrapping, the provisional dental
/// profiles, the ODEMedicationList, and the non-document reply resources (Appointment
/// from PCC-60, Communication from PCC-61). Resources embed a `_fullUrl` (stripped when
/// wrapped into a Bundle entry), mirroring the Python dict shape.</summary>
public static class Cow
{
    private static string Urn() => $"urn:uuid:{Guid.NewGuid()}";

    private const string UsCorePractitioner =
        "http://hl7.org/fhir/us/core/StructureDefinition/us-core-practitioner";
    private const string UsCoreOrganization =
        "http://hl7.org/fhir/us/core/StructureDefinition/us-core-organization";

    /// <summary>CodeableConcept for Task.businessStatus from the dental value set.</summary>
    public static JsonObject BusinessStatusConcept(string code)
    {
        var display = Config.CowBusinessStatus.TryGetValue(code, out var d) ? d : code;
        return new JsonObject
        {
            ["coding"] = new JsonArray
            { new JsonObject { ["system"] = Config.CowBusinessStatusSystem, ["code"] = code, ["display"] = display } },
            ["text"] = display,
        };
    }

    private static JsonObject? Concept(string system, IReadOnlyDictionary<string, string> table, string? code)
    {
        if (string.IsNullOrEmpty(code)) return null;
        if (table.TryGetValue(code, out var display))
            return new JsonObject
            {
                ["coding"] = new JsonArray
                { new JsonObject { ["system"] = system, ["code"] = code, ["display"] = display } },
                ["text"] = display,
            };
        return new JsonObject { ["text"] = code };   // unrecognized code -> carry the text
    }

    public static JsonObject? DeclineReasonConcept(string? code) =>
        Concept(Config.DeclineReasonSystem, Config.DeclineReasons, code);

    public static JsonObject? AppointmentTypeConcept(string? code) =>
        Concept(Config.AppointmentTypeSystem, Config.AppointmentTypes, code);

    public static JsonObject? NoshowReasonConcept(string? code) =>
        Concept(Config.NoshowReasonSystem, Config.NoshowReasons, code);

    public static JsonObject? ClearanceConcept(string? code) =>
        Concept(Config.ClearanceSystem, Config.ClearanceDispositions, code);

    /// <summary>Build the accepting provider's identity for Task.owner (PCC-56 accept).
    /// Returns (resources, ownerRef): resources to write + the PractitionerRole fullUrl.</summary>
    public static (List<JsonObject> resources, string? ownerRef) BuildOwnerRole(JsonObject? provider)
    {
        provider ??= new JsonObject();
        var any = new[] { "name", "npi", "specialty", "organization" }
            .Any(k => !string.IsNullOrEmpty(JsonX.Str(provider[k])));
        if (!any) return (new List<JsonObject>(), null);

        var pract = new JsonObject
        {
            ["resourceType"] = "Practitioner",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCorePractitioner } },
            ["name"] = new JsonArray { new JsonObject { ["text"] = JsonX.Str(provider["name"]) ?? "Accepting Provider" } },
        };
        var npi = JsonX.Str(provider["npi"]);
        if (!string.IsNullOrEmpty(npi))
            pract["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysNpi, ["value"] = npi } };
        var resources = new List<JsonObject> { pract };

        JsonObject? org = null;
        var orgName = JsonX.Str(provider["organization"]);
        if (!string.IsNullOrEmpty(orgName))
        {
            org = new JsonObject
            {
                ["resourceType"] = "Organization",
                ["_fullUrl"] = Urn(),
                ["name"] = orgName,
                ["meta"] = new JsonObject { ["profile"] = new JsonArray { UsCoreOrganization } },
            };
            resources.Add(org);
        }
        var role = new JsonObject
        {
            ["resourceType"] = "PractitionerRole",
            ["_fullUrl"] = Urn(),
            ["practitioner"] = new JsonObject { ["reference"] = JsonX.Str(pract["_fullUrl"]) },
        };
        if (org != null) role["organization"] = new JsonObject { ["reference"] = JsonX.Str(org["_fullUrl"]) };
        var spec = JsonX.Str(provider["specialty"]);
        if (!string.IsNullOrEmpty(spec))
            role["specialty"] = new JsonArray { new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject { ["system"] = Config.SysNucc, ["display"] = spec } },
                ["text"] = spec,
            } };
        resources.Add(role);
        return (resources, JsonX.Str(role["_fullUrl"]));
    }

    /// <summary>A single interim finding Observation for PCC-59 (in addition to the note).</summary>
    public static JsonObject InterimObservation(string referralId, string? patientRef,
                                                string? finding, string? value = null)
    {
        var obs = new JsonObject
        {
            ["resourceType"] = "Observation",
            ["_fullUrl"] = Urn(),
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "preliminary",
            ["code"] = new JsonObject { ["text"] = string.IsNullOrEmpty(finding) ? "Interim finding" : finding },
        };
        if (patientRef != null) obs["subject"] = new JsonObject { ["reference"] = patientRef };
        if (!string.IsNullOrEmpty(value)) obs["valueString"] = value;
        return obs;
    }

    /// <summary>A coded clearance/disposition Observation for PCC-57 outcome.</summary>
    public static JsonObject ClearanceObservation(string referralId, string? patientRef, string disposition)
    {
        var concept = ClearanceConcept(disposition) ?? new JsonObject { ["text"] = disposition };
        var obs = new JsonObject
        {
            ["resourceType"] = "Observation",
            ["_fullUrl"] = Urn(),
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "final",
            ["code"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = Config.SysLoinc, ["code"] = Config.ClearanceLoinc, ["display"] = "Referral clearance / disposition" } },
                ["text"] = "Referral clearance / disposition",
            },
            ["valueCodeableConcept"] = concept,
        };
        if (patientRef != null) obs["subject"] = new JsonObject { ["reference"] = patientRef };
        return obs;
    }

    /// <summary>The ODEMedicationList (`List`) aggregating the patient's current medications.
    /// Base-FHIR List (status=current, mode=snapshot, code=LOINC 10160-0) referencing the
    /// US Core MedicationRequests; referenced from ServiceRequest.supportingInfo.</summary>
    public static JsonObject BuildMedicationList(string referralId, string? patientRef, IEnumerable<string> medUrls)
    {
        var entry = new JsonArray();
        foreach (var u in medUrls) entry.Add(new JsonObject { ["item"] = new JsonObject { ["reference"] = u } });
        var lst = new JsonObject
        {
            ["resourceType"] = "List",
            ["_fullUrl"] = Urn(),
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { Config.ProfileOdeMedicationList } },
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "current",
            ["mode"] = "snapshot",
            ["title"] = "Medication List",
            ["code"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = Config.SysLoinc, ["code"] = Config.MedListLoinc, ["display"] = "History of Medication use Narrative" } },
                ["text"] = "Medication List",
            },
            ["entry"] = entry,
        };
        if (patientRef != null) lst["subject"] = new JsonObject { ["reference"] = patientRef };
        return lst;
    }

    /// <summary>Wrap result resources as Task.output entries (reference by fullUrl/id).</summary>
    public static JsonArray TaskOutput(IEnumerable<JsonObject>? resources)
    {
        var outArr = new JsonArray();
        foreach (var res in resources ?? Enumerable.Empty<JsonObject>())
        {
            var reference = JsonX.Str(res["_fullUrl"]);
            if (string.IsNullOrEmpty(reference))
            {
                var id = JsonX.Str(res["id"]);
                reference = id != null ? $"{JsonX.Str(res["resourceType"])}/{id}" : null;
            }
            if (string.IsNullOrEmpty(reference)) continue;
            outArr.Add(new JsonObject
            {
                ["type"] = new JsonObject { ["text"] = JsonX.Str(res["resourceType"]) },
                ["valueReference"] = new JsonObject { ["reference"] = reference },
            });
        }
        return outArr;
    }

    /// <summary>Stamp provisional ODE dental profiles on dental-flavored resources in place.</summary>
    public static void ApplyDentalProfiles(IEnumerable<JsonObject>? resources)
    {
        foreach (var res in resources ?? Enumerable.Empty<JsonObject>())
        {
            var rtype = JsonX.Str(res["resourceType"]);
            if (rtype == "Procedure" && IsCdt(res))
                AddProfile(res, Config.ProfileDentalProcedure);
            else if (rtype == "Observation" && (JsonX.Bool(res["_dental_perio"]) || ProfileHas(res, "perio")))
                AddProfile(res, Config.ProfilePerioObservation);
        }
    }

    /// <summary>COW reply resource for PCC-60 Appointment Notification.</summary>
    public static JsonObject BuildAppointment(string referralId, string? patientRef, string? taskRef,
        string? start, string status = "booked", string? end = null, string? location = null,
        string? provider = null, string? apptType = null)
    {
        var appt = new JsonObject
        {
            ["resourceType"] = "Appointment",
            ["_fullUrl"] = Urn(),
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = status,
        };
        if (!string.IsNullOrEmpty(start)) appt["start"] = Iso(start);
        if (!string.IsNullOrEmpty(end)) appt["end"] = Iso(end);
        var typeCc = AppointmentTypeConcept(apptType);
        if (typeCc != null) appt["appointmentType"] = typeCc;
        if (!string.IsNullOrEmpty(location)) appt["description"] = location;
        var parts = new JsonArray();
        if (patientRef != null)
            parts.Add(new JsonObject { ["actor"] = new JsonObject { ["reference"] = patientRef }, ["status"] = "accepted" });
        if (!string.IsNullOrEmpty(provider))
            parts.Add(new JsonObject { ["actor"] = new JsonObject { ["display"] = provider }, ["status"] = "accepted" });
        if (parts.Count > 0) appt["participant"] = parts;
        if (taskRef != null) appt["basedOn"] = new JsonArray { new JsonObject { ["reference"] = taskRef } };
        return appt;
    }

    /// <summary>COW reply resource for PCC-61 No-Show Notification.</summary>
    public static JsonObject BuildCommunication(string referralId, string? patientRef, string? taskRef,
        string reason, string? reasonCode = null, string? reschedule = null)
    {
        var payloads = new JsonArray { new JsonObject { ["contentString"] = reason } };
        if (!string.IsNullOrEmpty(reschedule))
            payloads.Add(new JsonObject { ["contentString"] = $"Reschedule: {reschedule}" });
        var comm = new JsonObject
        {
            ["resourceType"] = "Communication",
            ["_fullUrl"] = Urn(),
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = "completed",
            ["reasonCode"] = new JsonArray { NoshowReasonConcept(reasonCode) ?? new JsonObject { ["text"] = reason } },
            ["payload"] = payloads,
        };
        if (patientRef != null) comm["subject"] = new JsonObject { ["reference"] = patientRef };
        if (taskRef != null) comm["partOf"] = new JsonArray { new JsonObject { ["reference"] = taskRef } };
        return comm;
    }

    /// <summary>A COW Task resource snapshot for the harness inbox / export.</summary>
    public static JsonObject TaskSnapshot(string referralId, string? taskId, string status,
        string businessStatus, string? focusRef = null, string? patientRef = null,
        JsonArray? outputs = null, JsonNode? owner = null, JsonObject? statusReason = null,
        string? note = null, string? periodEnd = null)
    {
        var task = new JsonObject
        {
            ["resourceType"] = "Task",
            ["meta"] = new JsonObject { ["profile"] = new JsonArray { Config.ProfileCowTask } },
            ["identifier"] = new JsonArray { new JsonObject { ["system"] = Config.SysReferralId, ["value"] = referralId } },
            ["status"] = status,
            ["intent"] = "order",
            ["businessStatus"] = BusinessStatusConcept(businessStatus),
            ["code"] = new JsonObject
            {
                ["coding"] = new JsonArray { new JsonObject
                { ["system"] = "http://hl7.org/fhir/CodeSystem/task-code", ["code"] = "fulfill" } }
            },
        };
        if (!string.IsNullOrEmpty(taskId)) task["id"] = taskId;
        if (focusRef != null) task["focus"] = new JsonObject { ["reference"] = focusRef };
        if (patientRef != null) task["for"] = new JsonObject { ["reference"] = patientRef };
        if (owner != null)
        {
            var s = JsonX.Str(owner);
            task["owner"] = s != null ? new JsonObject { ["reference"] = s } : owner.DeepClone();
        }
        if (statusReason != null) task["statusReason"] = JsonX.Clone(statusReason);
        if (!string.IsNullOrEmpty(note)) task["note"] = new JsonArray { new JsonObject { ["text"] = note } };
        if (!string.IsNullOrEmpty(periodEnd))
            task["restriction"] = new JsonObject { ["period"] = new JsonObject { ["end"] = periodEnd } };
        if (outputs != null) task["output"] = (JsonArray)outputs.DeepClone();
        return task;
    }

    // --------------------------------------------------------------------- //
    /// <summary>Accept an HL7 v2 timestamp (YYYYMMDDHHMMSS) or pass through ISO-8601.</summary>
    public static string Iso(string? value)
    {
        var v = (value ?? "").Trim();
        if (v.Length >= 8 && v.All(char.IsDigit))
        {
            var outp = $"{v[..4]}-{v.Substring(4, 2)}-{v.Substring(6, 2)}";
            if (v.Length >= 12)
            {
                var sec = v.Length >= 14 ? v.Substring(12, 2) : "00";
                outp += $"T{v.Substring(8, 2)}:{v.Substring(10, 2)}:{sec}Z";
            }
            return outp;
        }
        return v;
    }

    private static bool IsCdt(JsonObject res)
    {
        var coding = JsonX.Arr(JsonX.Obj(res["code"])?["coding"]);
        if (coding == null) return false;
        foreach (var c in coding)
            if (JsonX.Str((c as JsonObject)?["system"]) == Config.SysCdt) return true;
        return false;
    }

    private static bool ProfileHas(JsonObject res, string needle)
    {
        var profs = JsonX.Arr(JsonX.Obj(res["meta"])?["profile"]);
        if (profs == null) return false;
        foreach (var p in profs)
        {
            var s = JsonX.Str(p);
            if (s != null && s.ToLowerInvariant().Contains(needle)) return true;
        }
        return false;
    }

    private static void AddProfile(JsonObject res, string profile)
    {
        var meta = JsonX.Obj(res["meta"]);
        if (meta == null) { meta = new JsonObject(); res["meta"] = meta; }
        var profs = JsonX.Arr(meta["profile"]);
        if (profs == null) { profs = new JsonArray(); meta["profile"] = profs; }
        if (!profs.Any(p => JsonX.Str(p) == profile)) profs.Add(profile);
    }
}
