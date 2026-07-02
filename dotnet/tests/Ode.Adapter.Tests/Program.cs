using System.Text.Json.Nodes;
using Ode.Adapter;
using Ode.Adapter.Plugins;
using Ode.Adapter.Ports;

// Dependency-free console test runner — mirrors a meaningful subset of
// python/tests/test_adapter.py. PASS/FAIL to stdout; non-zero exit on failure.

int passed = 0, failed = 0;
void Check(string name, bool ok)
{
    Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {name}");
    if (ok) passed++; else failed++;
}
void Throws(string name, Action a)
{
    try { a(); Check(name, false); }
    catch { Check(name, true); }
}

string Xml() => File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "referral_request.xml"));

Adapter NewAdapter()
{
    BuiltinPlugins.RegisterAll();
    var fhir = Registry.CreateFhir("generic-r4", new FhirBackendOptions { DryRun = true });
    return new Adapter(fhir, new JsonEnvelopeCodec(), new CaptureTransport());
}

JsonObject Inbound55(string referralId = "REF-1001") => new()
{
    ["direct_message_id"] = "MSG-1",
    ["submission_set_id"] = referralId,
    ["sender_direct_address"] = "ehr@hospital.direct.example",
    ["recipient_direct_address"] = "dental@clinic.direct.example",
    ["transaction"] = "PCC-55",
    ["hl7v2"] = $"MSH|^~\\&|EHR|ORG|ADAPTER|OHIA|20260101||OMG^O19|M1|P|2.5.1\rORC|NW|{referralId}\r",
    ["documents"] = new JsonArray
    { new JsonObject { ["id"] = "d1", ["mime_type"] = "text/xml", ["content"] = Xml() } },
};

string[] BundleTypes(JsonObject bundle) =>
    (bundle["entry"] as JsonArray)!
        .Select(e => (e as JsonObject)!["resource"]!["resourceType"]!.GetValue<string>())
        .ToArray();

JsonObject Task(string status, string referralId = "REF-1001") => new()
{
    ["resourceType"] = "Task",
    ["identifier"] = new JsonArray
    { new JsonObject { ["system"] = "urn:ohia:referral-id", ["value"] = referralId } },
    ["status"] = status,
};

JsonObject DentalProcedure() => new()
{
    ["resourceType"] = "Procedure",
    ["code"] = new JsonObject
    {
        ["coding"] = new JsonArray
        { new JsonObject { ["system"] = "http://www.ada.org/cdt", ["code"] = "D7140",
                           ["display"] = "Extraction, erupted tooth" } },
        ["text"] = "Extraction, erupted tooth",
    },
    ["bodySite"] = new JsonArray { new JsonObject { ["text"] = "19" } },
};

JsonObject MedicalProcedure() => new()
{
    ["resourceType"] = "Procedure",
    ["code"] = new JsonObject
    {
        ["coding"] = new JsonArray
        { new JsonObject { ["system"] = "http://snomed.info/sct", ["code"] = "80146002",
                           ["display"] = "Appendectomy" } },
        ["text"] = "Appendectomy",
    },
};

// ---------------------------- INBOUND ---------------------------- //
{
    var a = NewAdapter();
    var r = a.HandleInbound(Inbound55());
    Check("inbound returns referral_id", (string?)r["referral_id"] == "REF-1001");
    Check("inbound creates task_id", r["task_id"] is string);
    var types = BundleTypes((JsonObject)r["bundle"]!);
    Check("bundle has Patient", types.Contains("Patient"));
    Check("bundle has Practitioner", types.Contains("Practitioner"));
    Check("bundle has Condition", types.Contains("Condition"));
    Check("bundle has MedicationRequest", types.Contains("MedicationRequest"));
    Check("bundle has AllergyIntolerance", types.Contains("AllergyIntolerance"));
    Check("bundle has ServiceRequest", types.Contains("ServiceRequest"));
    Check("bundle has Task", types.Contains("Task"));
    Check("bundle has Provenance", types.Contains("Provenance"));

    // Task is requested, carries referral id
    var entries = (JsonObject)r["bundle"]!;
    var taskRes = (entries["entry"] as JsonArray)!
        .Select(e => (e as JsonObject)!["resource"] as JsonObject)
        .First(res => res!["resourceType"]!.GetValue<string>() == "Task")!;
    Check("task status requested", taskRes["status"]!.GetValue<string>() == "requested");
    var tid = (taskRes["identifier"] as JsonArray)![0]!["value"]!.GetValue<string>();
    Check("task carries referral id", tid == "REF-1001");

    // Tongue cancer condition present
    var condText = (entries["entry"] as JsonArray)!
        .Select(e => (e as JsonObject)!["resource"] as JsonObject)
        .Where(res => res!["resourceType"]!.GetValue<string>() == "Condition")
        .Select(res => res!["code"]?["text"]?.GetValue<string>() ?? "")
        .FirstOrDefault() ?? "";
    Check("condition mentions tongue/tumor",
        condText.ToLower().Contains("tongue") || condText.ToLower().Contains("tumor"));

    // Provenance stamped (not the 1970 placeholder)
    var prov = (entries["entry"] as JsonArray)!
        .Select(e => (e as JsonObject)!["resource"] as JsonObject)
        .First(res => res!["resourceType"]!.GetValue<string>() == "Provenance")!;
    Check("provenance recorded stamped",
        prov["recorded"]!.GetValue<string>() != "1970-01-01T00:00:00Z");
}

Throws("PCC-55 without CDA throws", () =>
{
    var a = NewAdapter();
    var env = Inbound55();
    env["documents"] = new JsonArray();
    a.HandleInbound(env);
});

Throws("unsupported inbound tx throws", () =>
{
    var a = NewAdapter();
    var env = Inbound55();
    env["transaction"] = "PCC-99";
    a.HandleInbound(env);
});

// ---------------------------- CANCELLATION ---------------------------- //
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var cancel = Inbound55();
    cancel["transaction"] = "PCC-58";
    var r = a.HandleInbound(cancel);
    Check("cancellation revokes task", (string?)r["action"] == "task_cancelled");

    var a2 = NewAdapter();
    var c2 = Inbound55("REF-NONE");
    c2["transaction"] = "PCC-58";
    var r2 = a2.HandleInbound(c2);
    Check("cancellation with no episode", (string?)r2["action"] == "cancellation_no_episode");
}

// ---------------------------- OUTBOUND ---------------------------- //
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("completed"), new List<JsonObject> { DentalProcedure() });
    Check("completed -> PCC-57", (string?)r["transaction"] == "PCC-57");
    var loss = (List<string>)r["loss_notes"]!;
    Check("dental result yields loss note", loss.Count > 0);
    var pkg = (JsonObject)r["packaged"]!;
    var cda = (pkg["documents"] as JsonArray)![0]!["content"]!.GetValue<string>();
    Check("PCC-57 carries a CDA document", cda.Contains("ClinicalDocument"));
    Check("CDT not coded in CDA (narrative only)", !cda.Contains("D7140"));
}

{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("in-progress"));
    Check("in-progress -> PCC-56 accept", (string?)r["transaction"] == "PCC-56");
}
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("rejected"));
    Check("rejected -> PCC-56 decline", (string?)r["transaction"] == "PCC-56");
}
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("in-progress"), new List<JsonObject> { DentalProcedure() }, interim: true);
    Check("interim -> PCC-59", (string?)r["transaction"] == "PCC-59");
}
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("cancelled"));
    Check("cancelled task -> PCC-58", (string?)r["transaction"] == "PCC-58");
}
{
    var a = NewAdapter();
    var r = a.HandleTaskEvent(Task("draft"));
    Check("unknown status -> no outbound", (string?)r["action"] == "no_outbound_for_status");
}
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleTaskEvent(Task("completed"), new List<JsonObject> { MedicalProcedure() });
    var loss = (List<string>)r["loss_notes"]!;
    Check("medical-only result yields no loss note", loss.Count == 0);
}

// ---------------------------- STATE MACHINE / REGISTRY ---------------------------- //
Check("state map: completed->PCC-57",
    StateMachine.TaskTo360X("completed", hasResult: true)!.Transaction == "PCC-57");
Check("state map: in-progress->PCC-56",
    StateMachine.TaskTo360X("in-progress")!.Transaction == "PCC-56");
Check("state map: unknown->null", StateMachine.TaskTo360X("draft") == null);

BuiltinPlugins.RegisterAll();
Check("registry lists generic-r4", Registry.Available("fhir").Contains("generic-r4"));
Check("registry lists onyx", Registry.Available("fhir").Contains("onyx"));
Check("registry lists json-envelope", Registry.Available("codec").Contains("json-envelope"));
Check("registry lists capture", Registry.Available("transport").Contains("capture"));

// from-config + onyx inbound + capture transport closed loop
{
    Environment.SetEnvironmentVariable("ODE_ADAPTER_FHIR_BACKEND", "onyx");
    var a = Adapter.FromConfig();
    Check("from_config selects onyx", a.Fhir.Name == "onyx");
    var r = a.HandleInbound(Inbound55());
    Check("onyx inbound creates referral", (string?)r["referral_id"] == "REF-1001");
    a.HandleTaskEvent(Task("completed"), new List<JsonObject> { DentalProcedure() });
    var cap = (CaptureTransport)a.Outbound;
    Check("capture transport recorded outbound", cap.Sent.Count == 1);
    Environment.SetEnvironmentVariable("ODE_ADAPTER_FHIR_BACKEND", null);
}

// ---------------------------- DENTAL-INITIATED + REPLIES ---------------------------- //
JsonObject RichMedToDental(string referralId = "REF-CONF") => new()
{
    ["referral_id"] = referralId,
    ["direction"] = "medical-to-dental",
    ["patient"] = new JsonObject { ["given"] = "John", ["family"] = "Smith", ["mrn"] = "MRN-9" },
    ["diagnoses"] = new JsonArray { new JsonObject
    { ["system"] = "icd10", ["code"] = "C02.1", ["display"] = "Malignant neoplasm of border of tongue" } },
    ["service"] = new JsonObject { ["system"] = "cdt", ["code"] = "D0150", ["display"] = "Comprehensive oral evaluation" },
    ["medications"] = new JsonArray
    {
        new JsonObject { ["code"] = "860975", ["display"] = "metformin 500 MG" },
        new JsonObject { ["code"] = "314076", ["display"] = "lisinopril 10 MG" },
    },
};

List<(string? url, JsonObject res)> Entries(JsonObject bundle, string rtype)
{
    var outList = new List<(string?, JsonObject)>();
    foreach (var e in (bundle["entry"] as JsonArray)!)
    {
        var eo = e as JsonObject;
        var r = eo!["resource"] as JsonObject;
        if (r!["resourceType"]!.GetValue<string>() == rtype)
            outList.Add((eo["fullUrl"]?.GetValue<string>(), r));
    }
    return outList;
}
List<JsonObject> FindAll(JsonObject bundle, string rtype) => Entries(bundle, rtype).Select(x => x.res).ToList();

string ConsultCda()
{
    var (cda, _) = FhirToCcda.BuildConsultationNote(null,
        new List<JsonObject> { new() { ["resourceType"] = "ClinicalImpression", ["summary"] = "Cleared for radiation." } });
    return cda;
}

JsonObject ReplyEnv(string tx, string referral, string? orderStatus = null, string? cda = null,
    string? appt = null, string? acceptingProvider = null, string? note = null,
    string? statusReason = null, string? periodEnd = null)
{
    var docs = new JsonArray();
    if (cda != null) docs.Add(new JsonObject { ["id"] = "d", ["mime_type"] = "text/xml", ["content"] = cda });
    var v2 = Hl7v2.Build(Hl7v2.TxMessageType[tx], referral, orderStatus, appointmentStart: appt,
        note: note, acceptingProvider: acceptingProvider, statusReason: statusReason, periodEnd: periodEnd);
    return new JsonObject
    {
        ["direct_message_id"] = "<r@x>",
        ["submission_set_id"] = "s",
        ["sender_direct_address"] = "med@x",
        ["recipient_direct_address"] = "dent@y",
        ["transaction"] = tx,
        ["hl7v2"] = v2,
        ["documents"] = docs,
    };
}

Adapter Initiated()
{
    var a = NewAdapter();
    a.HandleReferralInitiation("REF-D1", RichMedToDental("REF-D1"));
    return a;
}

// --- Dental-initiated PCC-55 (rich) ---
{
    var a = NewAdapter();
    var outb = a.HandleReferralInitiation("REF-D1", RichMedToDental("REF-D1"));
    Check("dental init -> PCC-55", (string?)outb["transaction"] == "PCC-55");
    var pkg = (JsonObject)outb["packaged"]!;
    var cda = (pkg["documents"] as JsonArray)![0]!["content"]!.GetValue<string>();
    Check("referral note doc code present", cda.Contains(Config.DocReferralNote));
    Check("referral note has reason section", cda.Contains("Reason for Referral"));
    var ep = a.Store.Get("REF-D1")!;
    Check("episode initiated_by dental", ep.InitiatedBy == "dental");
    Check("episode business_status referral-sent", ep.BusinessStatus == "referral-sent");
}

// --- Reply: PCC-56 accept / decline ---
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-56", "REF-D1", orderStatus: "IP"));
    Check("reply accept -> task accepted", (string?)r["task_status"] == "accepted");
    Check("reply accept -> businessStatus accepted", (string?)r["business_status"] == "accepted");
    Check("reply accept updates episode", a.Store.Get("REF-D1")!.Status == "accepted");
}
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-56", "REF-D1", orderStatus: "CA"));
    Check("reply decline -> task rejected", (string?)r["task_status"] == "rejected");
    Check("reply decline -> businessStatus declined", (string?)r["business_status"] == "declined");
    Check("reply decline -> request revoked", (string?)r["request_status"] == "revoked");
}
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-56", "REF-D1", orderStatus: "IP",
        acceptingProvider: "1841299305^Dr. Nadia Patel", note: "Accepted; patient will be seen.", periodEnd: "20260715"));
    var task = (JsonObject)r["task"]!;
    Check("accept records owner", JsonX.Str(JsonX.Obj(task["owner"])?["reference"]) != null);
    Check("accept carries note", JsonX.Str((task["note"] as JsonArray)?[0]?["text"]) == "Accepted; patient will be seen.");
    Check("accept carries period end", JsonX.Str(JsonX.Obj(JsonX.Obj(task["restriction"])?["period"])?["end"]) == "2026-07-15");
    var resTypes = ((List<JsonObject>)r["resources"]!).Select(x => JsonX.Str(x["resourceType"])).ToHashSet();
    Check("accept writes PractitionerRole", resTypes.Contains("PractitionerRole"));
}
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-56", "REF-D1", orderStatus: "CA",
        statusReason: "capacity", note: "No capacity this quarter."));
    var sr = (JsonObject)((JsonObject)r["task"]!)["statusReason"]!;
    var coding = (sr["coding"] as JsonArray)![0] as JsonObject;
    Check("decline coded statusReason system", JsonX.Str(coding!["system"]) == Config.DeclineReasonSystem);
    Check("decline coded statusReason code", JsonX.Str(coding["code"]) == "capacity");
}

// --- Reply: PCC-57 outcome / PCC-59 interim ---
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-57", "REF-D1", cda: ConsultCda()));
    Check("outcome -> task completed", (string?)r["task_status"] == "completed");
    Check("outcome -> businessStatus outcome-final", (string?)r["business_status"] == "outcome-final");
    Check("outcome -> request completed", (string?)r["request_status"] == "completed");
    var types = ((List<JsonObject>)r["resources"]!).Select(x => JsonX.Str(x["resourceType"])).ToHashSet();
    Check("outcome writes ClinicalImpression + DocumentReference",
        types.Contains("ClinicalImpression") && types.Contains("DocumentReference"));
}
Throws("PCC-57 without consultation note throws", () =>
{
    var a = Initiated();
    a.HandleInbound(ReplyEnv("PCC-57", "REF-D1"));
});
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-59", "REF-D1", cda: ConsultCda()));
    Check("interim -> in-progress", (string?)r["task_status"] == "in-progress");
    Check("interim -> businessStatus interim-results", (string?)r["business_status"] == "interim-results");
}

// --- Reply: PCC-60 appointment / PCC-61 no-show ---
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-60", "REF-D1", appt: "20260715090000"));
    Check("appointment -> businessStatus appointment-booked", (string?)r["business_status"] == "appointment-booked");
    var appt = ((List<JsonObject>)r["resources"]!)[0];
    Check("appointment resource booked", JsonX.Str(appt["resourceType"]) == "Appointment" && JsonX.Str(appt["status"]) == "booked");
    Check("appointment start iso", (JsonX.Str(appt["start"]) ?? "").StartsWith("2026-07-15"));
}
{
    var a = Initiated();
    var r = a.HandleInbound(ReplyEnv("PCC-61", "REF-D1"));
    Check("noshow -> businessStatus appointment-noshow", (string?)r["business_status"] == "appointment-noshow");
    Check("noshow writes Communication", JsonX.Str(((List<JsonObject>)r["resources"]!)[0]["resourceType"]) == "Communication");
}
{
    var a = NewAdapter();
    var r = a.HandleInbound(ReplyEnv("PCC-56", "REF-ORPHAN", orderStatus: "IP"));
    Check("reply without prior initiation is safe", (string?)r["task_status"] == "accepted");
    Check("orphan episode opened", a.Store.Get("REF-ORPHAN") != null);
}
{
    var a = Initiated();
    a.HandleInbound(ReplyEnv("PCC-57", "REF-D1", cda: ConsultCda()));
    var ep = a.Store.Get("REF-D1")!;
    Check("reply ingestion caches inbox", ep.Inbox.Count > 0);
    Check("inbox has DocumentReference", ep.Inbox.Any(r => JsonX.Str(r["resourceType"]) == "DocumentReference"));
}

// --- reply_to_fhir mapping + appointment_event ---
Check("reply map PCC-56 IP accepted", StateMachine.ReplyToFhir("PCC-56", "IP")!.TaskStatus == "accepted");
Check("reply map PCC-56 CA rejected", StateMachine.ReplyToFhir("PCC-56", "CA")!.TaskStatus == "rejected");
Check("reply map PCC-57 outcome-final", StateMachine.ReplyToFhir("PCC-57")!.BusinessStatus == "outcome-final");
Check("reply map PCC-59 in-progress", StateMachine.ReplyToFhir("PCC-59")!.TaskStatus == "in-progress");
Check("reply map PCC-60 appointment-booked", StateMachine.ReplyToFhir("PCC-60")!.BusinessStatus == "appointment-booked");
Check("reply map PCC-61 appointment-noshow", StateMachine.ReplyToFhir("PCC-61")!.BusinessStatus == "appointment-noshow");
Check("reply map PCC-55 null", StateMachine.ReplyToFhir("PCC-55") == null);
Check("appointment_event no_show=false -> PCC-60", StateMachine.AppointmentEvent(false).Transaction == "PCC-60");
Check("appointment_event no_show=true -> PCC-61", StateMachine.AppointmentEvent(true).Transaction == "PCC-61");

// --- Outbound appointment / no-show events ---
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleAppointmentEvent("REF-1001", appointmentStart: "20260715090000", provider: "Dr. Molar");
    Check("appointment-event -> PCC-60", (string?)r["transaction"] == "PCC-60");
    Check("appointment-event businessStatus", (string?)r["business_status"] == "appointment-booked");
}
{
    var a = NewAdapter();
    a.HandleInbound(Inbound55());
    var r = a.HandleAppointmentEvent("REF-1001", noShow: true, reason: "no-show");
    Check("noshow-event -> PCC-61", (string?)r["transaction"] == "PCC-61");
    Check("noshow-event businessStatus", (string?)r["business_status"] == "appointment-noshow");
}

// ---------------------------- ODE-CONTRACT CONFORMANCE ---------------------------- //
{
    var b = ReferralFhir.BuildReferralBundle(RichMedToDental());
    var sr = FindAll(b, "ServiceRequest")[0];
    var profiles = (sr["meta"] as JsonObject)!["profile"] as JsonArray;
    Check("med->dental SR profile", profiles!.Count == 1 && JsonX.Str(profiles[0]) == Config.ProfileOdeMedToDental);
    var systems = ((sr["code"] as JsonObject)!["coding"] as JsonArray ?? new JsonArray())
        .Select(c => JsonX.Str((c as JsonObject)?["system"])).ToHashSet();
    Check("med->dental drops CDT", !systems.Contains(Config.SysCdt));
    var reasonSystems = (sr["reasonCode"] as JsonArray ?? new JsonArray())
        .SelectMany(rc => ((rc as JsonObject)?["coding"] as JsonArray) ?? new JsonArray())
        .Select(c => JsonX.Str((c as JsonObject)?["system"])).ToHashSet();
    Check("med->dental ICD-10 reasonCode", reasonSystems.Contains(Config.SysIcd10));
}
{
    var b = ReferralFhir.BuildReferralBundle(RichMedToDental());
    var lists = Entries(b, "List");
    Check("exactly one ODEMedicationList", lists.Count == 1);
    var (listUrl, medList) = lists[0];
    var listProfiles = (medList["meta"] as JsonObject)!["profile"] as JsonArray;
    Check("med list profile", JsonX.Str(listProfiles![0]) == Config.ProfileOdeMedicationList);
    Check("med list status/mode", JsonX.Str(medList["status"]) == "current" && JsonX.Str(medList["mode"]) == "snapshot");
    Check("med list LOINC", JsonX.Str(((medList["code"] as JsonObject)!["coding"] as JsonArray)![0]!["code"]) == Config.MedListLoinc);
    var mrUrls = Entries(b, "MedicationRequest").Select(x => x.url).ToHashSet();
    var medRefs = (medList["entry"] as JsonArray)!
        .Select(e => JsonX.Str((e as JsonObject)?["item"]?["reference"])).ToHashSet();
    Check("med list references all MedicationRequests", medRefs.SetEquals(mrUrls) && medRefs.Count == 2);
    var sr = FindAll(b, "ServiceRequest")[0];
    var support = (sr["supportingInfo"] as JsonArray ?? new JsonArray())
        .Select(s => JsonX.Str((s as JsonObject)?["reference"])).ToHashSet();
    Check("SR supportingInfo references the List", support.Contains(listUrl));
}
{
    var b = ReferralFhir.BuildReferralBundle(RichMedToDental());
    var task = FindAll(b, "Task")[0];
    var tProfiles = (task["meta"] as JsonObject)!["profile"] as JsonArray;
    Check("Task profile ode-referral-task", JsonX.Str(tProfiles![0]) == Config.ProfileOdeReferralTask);
    var bs = ((task["businessStatus"] as JsonObject)!["coding"] as JsonArray)![0] as JsonObject;
    Check("Task businessStatus system", JsonX.Str(bs!["system"]) == "http://ohia-codes.org/CodeSystem/ode-referral-sub-status");
    Check("Task businessStatus code received", JsonX.Str(bs["code"]) == "received");
}
{
    var rich = RichMedToDental("REF-D2D");
    rich["direction"] = "dental-to-dental";
    rich["service"] = new JsonObject { ["system"] = "cdt", ["code"] = "D7210", ["display"] = "Surgical extraction" };
    var b = ReferralFhir.BuildReferralBundle(rich);
    var sr = FindAll(b, "ServiceRequest")[0];
    var profiles = (sr["meta"] as JsonObject)!["profile"] as JsonArray;
    Check("dental->dental SR profile", JsonX.Str(profiles![0]) == Config.ProfileOdeDentalToDental);
    var systems = ((sr["code"] as JsonObject)!["coding"] as JsonArray ?? new JsonArray())
        .Select(c => JsonX.Str((c as JsonObject)?["system"])).ToHashSet();
    Check("dental->dental keeps CDT", systems.Contains(Config.SysCdt));
}

Console.WriteLine($"\n{passed} passed, {failed} failed, {passed + failed} total");
return failed == 0 ? 0 : 1;
