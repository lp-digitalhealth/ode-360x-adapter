using System.Text.Json.Nodes;
using Ode.Adapter;

// Minimal API host — mirrors python/ode_adapter/app.py.
// Run: dotnet run --project src/Ode.Adapter.Host   (listens on http://localhost:5000 by default)

var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

var adapter = Adapter.FromConfig();

app.MapGet("/healthz", () => Results.Json(new { status = "ok" }));

app.MapGet("/plugins", () => Results.Json(Registry.AllPlugins()));

app.MapGet("/episodes", () =>
{
    var arr = new JsonArray();
    foreach (var e in adapter.Store.All()) arr.Add(JsonX.Clone(e));
    return Results.Text(arr.ToJsonString(), "application/json");
});

// POST an inbound 360X envelope (PCC-55 / PCC-58).
app.MapPost("/360x/inbound", (JsonObject body) =>
{
    try
    {
        var result = adapter.HandleInbound(body);
        return Results.Text(ToJson(result), "application/json");
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

// POST an ODE Task state change to emit outbound 360X.
// Body: { "task": {...}, "result_resources": [...], "patient": {...}, "interim": false }
app.MapPost("/ode/task-event", (JsonObject body) =>
{
    try
    {
        var task = body["task"] as JsonObject
                   ?? throw new ArgumentException("missing 'task'");
        var results = (body["result_resources"] as JsonArray)?
            .Select(n => (JsonObject)n!).ToList();
        var patient = body["patient"] as JsonObject;
        var interim = body["interim"] is JsonValue iv && iv.TryGetValue<bool>(out var b) && b;
        var result = adapter.HandleTaskEvent(task, results, patient, interim);
        return Results.Text(ToJson(result), "application/json");
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

// POST a dental-initiated referral intake -> outbound 360X PCC-55 (Referral Request).
// Body is the rich ReferralInitiation shape (referral_id, direction, patient, coverage,
// referring_provider, rendering_provider, service, diagnoses, medications, ...).
app.MapPost("/ode/referral", (JsonObject body) =>
{
    try
    {
        var referralId = JsonX.Str(body["referral_id"])
                         ?? throw new ArgumentException("missing 'referral_id'");
        var result = adapter.HandleReferralInitiation(referralId, body,
            JsonX.Str(body["sender"]), JsonX.Str(body["recipient"]));
        return Results.Text(ToJson(result), "application/json");
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

// POST an outbound scheduling event -> PCC-60 Appointment / PCC-61 No-Show.
app.MapPost("/ode/appointment-event", (JsonObject body) =>
{
    try
    {
        var referralId = JsonX.Str(body["referral_id"])
                         ?? throw new ArgumentException("missing 'referral_id'");
        var noShow = body["no_show"] is JsonValue nv && nv.TryGetValue<bool>(out var ns) && ns;
        var result = adapter.HandleAppointmentEvent(referralId, noShow,
            JsonX.Str(body["appointment_start"]), JsonX.Str(body["appointment_end"]),
            JsonX.Str(body["location"]), JsonX.Str(body["provider"]),
            JsonX.Str(body["appt_type"]), JsonX.Str(body["reason"]), JsonX.Str(body["reschedule"]));
        return Results.Text(ToJson(result), "application/json");
    }
    catch (Exception ex)
    {
        return Results.BadRequest(new { error = ex.Message });
    }
});

// GET the FHIR a PMS/EHR would read for a referral (live find-by-referral, else the
// per-episode dry-run cache the bridge accumulated).
app.MapGet("/episodes/{referralId}/inbox", (string referralId) =>
{
    var live = adapter.Fhir.FindByReferral(referralId);
    var ep = adapter.Store.Get(referralId);
    var resources = new JsonArray();
    var source = live.Count > 0 ? "fhir-server" : "episode-cache";
    if (live.Count > 0)
        foreach (var r in live) resources.Add(JsonX.Clone(r));
    else if (ep != null)
        foreach (var r in ep.Inbox) resources.Add(JsonX.Clone(r));
    var o = new JsonObject
    {
        ["referral_id"] = referralId,
        ["source"] = source,
        ["status"] = ep?.Status,
        ["business_status"] = ep?.BusinessStatus,
        ["resources"] = resources,
    };
    return Results.Text(o.ToJsonString(), "application/json");
});

app.Run();

// Serialize a handler result (Dictionary<string, object?>) to JSON, cloning JsonNodes
// so nothing is reparented.
static string ToJson(Dictionary<string, object?> result)
{
    var o = new JsonObject();
    foreach (var (k, v) in result)
    {
        o[k] = v switch
        {
            null => null,
            JsonObject jo => JsonX.Clone(jo),
            JsonArray ja => (JsonArray)JsonNode.Parse(ja.ToJsonString())!,
            List<string> ls => new JsonArray(ls.Select(s => (JsonNode)s!).ToArray()),
            List<JsonObject> lo => new JsonArray(lo.Select(r => (JsonNode)JsonX.Clone(r)).ToArray()),
            string s => s,
            bool b => b,
            _ => v.ToString(),
        };
    }
    return o.ToJsonString();
}
