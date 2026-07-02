using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Ode.Adapter.Ports;

namespace Ode.Adapter.Plugins;

/// <summary>Generic FHIR R4 backend — any conformant R4 server (e.g. HAPI).
/// Mirrors plugins/fhir/generic_r4.py. Dry-run echoes the bundle with synthetic ids.</summary>
public class GenericR4Backend : IFhirBackend
{
    private static readonly HttpClient Http = new();

    public string Name { get; protected set; } = "generic-r4";
    protected readonly string BaseUrl;
    protected readonly bool DryRun;
    protected string LoadMode;

    public GenericR4Backend(FhirBackendOptions o)
    {
        BaseUrl = (o.BaseUrl ?? Config.Settings.OdeNativeBaseUrl).TrimEnd('/');
        DryRun = o.DryRun ?? Config.Settings.DryRun;
        LoadMode = o.LoadMode;
    }

    public virtual JsonObject SubmitReferralBundle(JsonObject bundle)
    {
        if (DryRun) return Echo(bundle);
        return PostJson(BaseUrl, bundle, "application/fhir+json");
    }

    public virtual JsonObject UpdateTaskStatus(string taskId, string status, string? reason = null,
        string? businessStatus = null, JsonArray? outputs = null, JsonNode? owner = null,
        JsonObject? statusReason = null, string? note = null, string? periodEnd = null)
    {
        var statusReasonVal = statusReason
            ?? (reason != null ? new JsonObject { ["text"] = reason } : null);
        var ownerVal = OwnerVal(owner);
        if (DryRun)
        {
            var task = new JsonObject { ["resourceType"] = "Task", ["id"] = taskId, ["status"] = status };
            if (statusReasonVal != null) task["statusReason"] = JsonX.Clone(statusReasonVal);
            if (businessStatus != null) task["businessStatus"] = Cow.BusinessStatusConcept(businessStatus);
            if (ownerVal != null) task["owner"] = ownerVal.DeepClone();
            if (note != null) task["note"] = new JsonArray { new JsonObject { ["text"] = note } };
            if (periodEnd != null)
                task["restriction"] = new JsonObject { ["period"] = new JsonObject { ["end"] = periodEnd } };
            if (outputs != null) task["output"] = (JsonArray)outputs.DeepClone();
            return task;
        }
        var patch = new JsonArray
        {
            new JsonObject { ["op"] = "replace", ["path"] = "/status", ["value"] = status }
        };
        if (statusReasonVal != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/statusReason", ["value"] = JsonX.Clone(statusReasonVal) });
        if (businessStatus != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/businessStatus", ["value"] = Cow.BusinessStatusConcept(businessStatus) });
        if (ownerVal != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/owner", ["value"] = ownerVal.DeepClone() });
        if (note != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/note", ["value"] = new JsonArray { new JsonObject { ["text"] = note } } });
        if (periodEnd != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/restriction", ["value"] = new JsonObject { ["period"] = new JsonObject { ["end"] = periodEnd } } });
        if (outputs != null)
            patch.Add(new JsonObject { ["op"] = "add", ["path"] = "/output", ["value"] = (JsonArray)outputs.DeepClone() });
        var req = new HttpRequestMessage(new HttpMethod("PATCH"), $"{BaseUrl}/Task/{taskId}")
        {
            Content = new StringContent(patch.ToJsonString(), Encoding.UTF8, "application/json-patch+json")
        };
        var resp = Http.Send(req);
        resp.EnsureSuccessStatusCode();
        return (JsonObject)JsonNode.Parse(Read(resp))!;
    }

    private static JsonNode? OwnerVal(JsonNode? owner)
    {
        if (owner == null) return null;
        var s = JsonX.Str(owner);
        return s != null ? new JsonObject { ["reference"] = s } : owner;
    }

    public virtual JsonObject UpdateRequestStatus(string requestId, string status, string? reason = null)
    {
        if (DryRun)
        {
            var sr = new JsonObject { ["resourceType"] = "ServiceRequest", ["id"] = requestId, ["status"] = status };
            if (reason != null) sr["_status_reason"] = reason;
            return sr;
        }
        var patch = new JsonArray
        {
            new JsonObject { ["op"] = "replace", ["path"] = "/status", ["value"] = status }
        };
        var req = new HttpRequestMessage(new HttpMethod("PATCH"), $"{BaseUrl}/ServiceRequest/{requestId}")
        {
            Content = new StringContent(patch.ToJsonString(), Encoding.UTF8, "application/json-patch+json")
        };
        var resp = Http.Send(req);
        resp.EnsureSuccessStatusCode();
        return (JsonObject)JsonNode.Parse(Read(resp))!;
    }

    public virtual JsonObject GetTask(string taskId)
    {
        if (DryRun)
            return new JsonObject { ["resourceType"] = "Task", ["id"] = taskId, ["status"] = "requested" };
        var resp = Http.Send(new HttpRequestMessage(HttpMethod.Get, $"{BaseUrl}/Task/{taskId}"));
        resp.EnsureSuccessStatusCode();
        return (JsonObject)JsonNode.Parse(Read(resp))!;
    }

    public virtual List<JsonObject> FindByReferral(string referralId)
    {
        var outList = new List<JsonObject>();
        if (DryRun) return outList;
        var ident = $"{Config.SysReferralId}|{referralId}";
        foreach (var rtype in new[] { "Task", "ServiceRequest" })
        {
            // `referral-id` is the contract's token search param; `identifier` is the fallback.
            var url = $"{BaseUrl}/{rtype}?referral-id={Uri.EscapeDataString(ident)}" +
                      $"&identifier={Uri.EscapeDataString(ident)}&_include=*&_revinclude=*";
            var resp = Http.Send(new HttpRequestMessage(HttpMethod.Get, url));
            if (resp.StatusCode != HttpStatusCode.OK) continue;
            var body = JsonNode.Parse(Read(resp)) as JsonObject;
            foreach (var e in JsonX.Arr(body?["entry"]) ?? new JsonArray())
            {
                var res = JsonX.Obj((e as JsonObject)?["resource"]);
                if (res != null) outList.Add(res);
            }
        }
        return outList;
    }

    protected static JsonObject PostJson(string url, JsonNode body, string contentType)
    {
        var req = new HttpRequestMessage(HttpMethod.Post, url)
        {
            Content = new StringContent(body.ToJsonString(), Encoding.UTF8, contentType)
        };
        var resp = Http.Send(req);
        resp.EnsureSuccessStatusCode();
        return (JsonObject)JsonNode.Parse(Read(resp))!;
    }

    protected static string Read(HttpResponseMessage resp) =>
        resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();

    protected static JsonObject Echo(JsonObject bundle)
    {
        var entries = new JsonArray();
        var arr = JsonX.Arr(bundle["entry"]) ?? new JsonArray();
        for (int i = 0; i < arr.Count; i++)
        {
            var resOrig = JsonX.Obj((arr[i] as JsonObject)?["resource"]);
            if (resOrig == null) continue;
            var res = JsonX.Clone(resOrig);
            var rtype = JsonX.Str(res["resourceType"])!;
            res["id"] = $"{rtype.ToLowerInvariant()}-{i + 1}";
            entries.Add(new JsonObject
            {
                ["response"] = new JsonObject
                {
                    ["status"] = "201 Created",
                    ["location"] = $"{rtype}/{JsonX.Str(res["id"])}"
                },
                ["resource"] = res,
            });
        }
        return new JsonObject
        {
            ["resourceType"] = "Bundle",
            ["type"] = "transaction-response",
            ["entry"] = entries,
        };
    }
}

/// <summary>OnyxOS backend — example server-specific plugin (PUT/UPSERT load).
/// Mirrors plugins/fhir/onyx.py. Validate the upsert path against a live OnyxOS server.</summary>
public sealed class OnyxBackend : GenericR4Backend
{
    public OnyxBackend(FhirBackendOptions o) : base(Adjust(o)) { Name = "onyx"; }

    private static FhirBackendOptions Adjust(FhirBackendOptions o)
    {
        if (string.IsNullOrEmpty(o.LoadMode) || o.LoadMode == "transaction") o.LoadMode = "upsert";
        return o;
    }

    public override JsonObject SubmitReferralBundle(JsonObject bundle)
    {
        if (DryRun || LoadMode != "upsert") return base.SubmitReferralBundle(bundle);
        var responses = new JsonArray();
        foreach (var (url, resource) in ToUpsertPuts(bundle))
        {
            var put = PostJson($"{BaseUrl}/{url}", resource, "application/fhir+json"); // PUT in production
            responses.Add(new JsonObject
            {
                ["response"] = new JsonObject { ["status"] = "200", ["location"] = url },
                ["resource"] = put,
            });
        }
        return new JsonObject
        {
            ["resourceType"] = "Bundle",
            ["type"] = "transaction-response",
            ["entry"] = responses,
        };
    }

    private static List<(string url, JsonObject resource)> ToUpsertPuts(JsonObject bundle)
    {
        var idmap = new Dictionary<string, string>();
        var arr = JsonX.Arr(bundle["entry"]) ?? new JsonArray();
        foreach (var e in arr)
        {
            var res = JsonX.Obj((e as JsonObject)?["resource"]);
            if (res == null) continue;
            var rid = JsonX.Str(res["id"]) ?? Guid.NewGuid().ToString();
            res["id"] = rid;
            var fullUrl = JsonX.Str((e as JsonObject)?["fullUrl"]);
            if (fullUrl != null) idmap[fullUrl] = $"{JsonX.Str(res["resourceType"])}/{rid}";
        }
        var puts = new List<(string, JsonObject)>();
        foreach (var e in arr)
        {
            var res = JsonX.Obj((e as JsonObject)?["resource"]);
            if (res == null) continue;
            Rewrite(res, idmap);
            puts.Add(($"{JsonX.Str(res["resourceType"])}/{JsonX.Str(res["id"])}", JsonX.Clone(res)));
        }
        return puts;
    }

    private static void Rewrite(JsonNode? node, Dictionary<string, string> idmap)
    {
        if (node is JsonObject o)
        {
            if (o.Count == 1 && o["reference"] is JsonValue) // {"reference": "..."}
            {
                var r = JsonX.Str(o["reference"]);
                if (r != null && idmap.TryGetValue(r, out var mapped)) o["reference"] = mapped;
            }
            else
                foreach (var kv in o.ToList()) Rewrite(kv.Value, idmap);
        }
        else if (node is JsonArray a)
        {
            foreach (var item in a.ToList()) Rewrite(item, idmap);
        }
    }
}
