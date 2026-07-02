using System.Text.Json;
using System.Text.Json.Nodes;
using Ode.Adapter;

// Console demo — mirrors python/samples/demo.py. Runs fully in dry-run (no FHIR server).

var xml = File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "referral_request.xml"));

var adapter = Adapter.FromConfig();
Console.WriteLine($"Available plugins: {JsonSerializer.Serialize(adapter.Fhir.Name)} backend; " +
                  $"codec={adapter.Codec.Name}; transport={adapter.Outbound.Name}\n");

// ---- INBOUND: PCC-55 Referral Request (medical -> dental) ----
var inboundEnvelope = new JsonObject
{
    ["direct_message_id"] = "MSG-DEMO-1",
    ["submission_set_id"] = "REF-1001",
    ["sender_direct_address"] = "oncology@hospital.direct.example",
    ["recipient_direct_address"] = "referrals@dentalclinic.direct.example",
    ["transaction"] = "PCC-55",
    ["hl7v2"] = "MSH|^~\\&|EHR|ORG|ADAPTER|OHIA|20260101000000||OMG^O19|MID1|P|2.5.1\rORC|NW|REF-1001\r",
    ["documents"] = new JsonArray
    {
        new JsonObject { ["id"] = "doc-1", ["mime_type"] = "text/xml", ["content"] = xml }
    },
};

var inbound = adapter.HandleInbound(inboundEnvelope);
Console.WriteLine("=== INBOUND PCC-55 ===");
Console.WriteLine($"referral_id        : {inbound["referral_id"]}");
Console.WriteLine($"task_id            : {inbound["task_id"]}");
Console.WriteLine($"service_request_id : {inbound["service_request_id"]}");
var bundle = (JsonObject)inbound["bundle"]!;
var types = (bundle["entry"] as JsonArray)!
    .Select(e => (e as JsonObject)?["resource"]?["resourceType"]?.GetValue<string>())
    .Where(t => t != null);
Console.WriteLine($"bundle resources   : {string.Join(", ", types)}\n");

// ---- OUTBOUND: Task completed with a dental result (dental -> medical) ----
var task = new JsonObject
{
    ["resourceType"] = "Task",
    ["identifier"] = new JsonArray
    { new JsonObject { ["system"] = "urn:ohia:referral-id", ["value"] = "REF-1001" } },
    ["status"] = "completed",
};
var procedure = new JsonObject
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

var outbound = adapter.HandleTaskEvent(task, new List<JsonObject> { procedure });
Console.WriteLine("=== OUTBOUND (completed -> PCC-57) ===");
Console.WriteLine($"transaction : {outbound["transaction"]}");
Console.WriteLine($"loss_notes  : {JsonSerializer.Serialize(outbound["loss_notes"])}");
var packaged = (JsonObject)outbound["packaged"]!;
Console.WriteLine($"v2          : {packaged["hl7v2"]!.GetValue<string>().Split('\r')[0]}");

// ---- DENTAL-INITIATED: a rich referral -> outbound PCC-55 (dental -> medical) ----
var rich = new JsonObject
{
    ["direction"] = "dental-to-medical",
    ["priority"] = "urgent",
    ["reason_text"] = "Suspicious oral lesion; requesting ENT evaluation for possible malignancy.",
    ["patient"] = new JsonObject
    { ["mrn"] = "MRN-558211", ["given"] = "Wilma", ["family"] = "Stonewright", ["gender"] = "female" },
    ["coverage"] = new JsonObject { ["payer"] = "Delta Health", ["member_id"] = "DH-99123" },
    ["referring_provider"] = new JsonObject
    { ["name"] = "Dr. Molar", ["npi"] = "1770001234", ["organization"] = "Dental Group" },
    ["rendering_provider"] = new JsonObject { ["name"] = "Dr. Nguyen (ENT)", ["organization"] = "Oncology Assoc." },
    ["service"] = new JsonObject { ["system"] = "cpt", ["code"] = "99243", ["display"] = "Office consultation" },
    ["diagnoses"] = new JsonArray
    { new JsonObject { ["system"] = "icd10", ["code"] = "K13.79", ["display"] = "Other lesions of oral mucosa" } },
    ["medications"] = new JsonArray
    { new JsonObject { ["system"] = "rxnorm", ["code"] = "197361", ["display"] = "Amoxicillin 500 mg" } },
    ["supporting_info"] = "2-week history of a non-healing ulcer on the lateral tongue.",
};
var referral = adapter.HandleReferralInitiation("REF-2002", rich);
Console.WriteLine("\n=== DENTAL-INITIATED REFERRAL (PCC-55 out) ===");
Console.WriteLine($"transaction : {referral["transaction"]}");
var refBundle = (JsonObject)referral["bundle"]!;
var refTypes = (refBundle["entry"] as JsonArray)!
    .Select(e => (e as JsonObject)?["resource"]?["resourceType"]?.GetValue<string>()).Where(t => t != null);
Console.WriteLine($"bundle      : {string.Join(", ", refTypes)}");

// ---- INBOUND REPLY: the medical peer accepts REF-2002 (PCC-56 accept) ----
var replyEnvelope = new JsonObject
{
    ["direct_message_id"] = "MSG-REPLY-1",
    ["submission_set_id"] = "REF-2002",
    ["sender_direct_address"] = "referrals@oncology.direct.example.org",
    ["recipient_direct_address"] = "intake@dentalgroup.direct.example.org",
    ["transaction"] = "PCC-56",
    ["hl7v2"] = Hl7v2.Build("OSU^O51", "REF-2002", "IP",
        acceptingProvider: "1990002345^Dr. Nguyen", periodEnd: "20260715"),
    ["documents"] = new JsonArray(),
};
var reply = adapter.HandleInbound(replyEnvelope);
Console.WriteLine("\n=== INBOUND REPLY (PCC-56 accept) ===");
Console.WriteLine($"task_status     : {reply["task_status"]}");
Console.WriteLine($"business_status : {reply["business_status"]}");

Console.WriteLine("\nDemo complete (dry-run; no live FHIR server contacted).");
