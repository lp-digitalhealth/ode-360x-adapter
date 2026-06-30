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

Console.WriteLine($"\n{passed} passed, {failed} failed, {passed + failed} total");
return failed == 0 ? 0 : 1;
