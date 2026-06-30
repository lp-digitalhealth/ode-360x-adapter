using System.Text.Json.Nodes;

namespace Ode.Adapter;

/// <summary>Null-safe helpers over System.Text.Json.Nodes (Python dicts are JsonObjects here).</summary>
public static class JsonX
{
    public static string? Str(JsonNode? n) =>
        n is JsonValue v && v.TryGetValue<string>(out var s) ? s : null;

    public static JsonObject? Obj(JsonNode? n) => n as JsonObject;
    public static JsonArray? Arr(JsonNode? n) => n as JsonArray;

    public static bool Bool(JsonNode? n) =>
        n is JsonValue v && v.TryGetValue<bool>(out var b) && b;

    /// <summary>Deep-clone (a JsonNode may only have one parent).</summary>
    public static JsonObject Clone(JsonObject o) => (JsonObject)JsonNode.Parse(o.ToJsonString())!;
}
