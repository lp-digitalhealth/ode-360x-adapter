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
            string s => s,
            bool b => b,
            _ => v.ToString(),
        };
    }
    return o.ToJsonString();
}
