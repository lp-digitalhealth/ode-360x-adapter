using System.Text.Json.Nodes;

namespace Ode.Adapter;

/// <summary>One referral episode. Mirrors stores.Episode.</summary>
public sealed class Episode
{
    public string ReferralId { get; init; } = "";
    public string DirectMessageId { get; init; } = "";
    public string SubmissionSetId { get; init; } = "";
    public string SenderDirectAddress { get; init; } = "";
    public string RecipientDirectAddress { get; init; } = "";
    public string? TaskId { get; set; }
    public string? ServiceRequestId { get; set; }
    public string Status { get; set; } = "requested";
    // COW: granular dental business-status (see Config.CowBusinessStatus).
    public string? BusinessStatus { get; set; }
    // Which side opened this episode in the harness: "dental" or "medical".
    public string? InitiatedBy { get; set; }
    public string Created { get; } = DateTime.UtcNow.ToString("o");
    public List<string> History { get; } = new();
    // The FHIR the bridge wrote for this episode, so a harness "inbox" can read it
    // (in dry-run there is no live server to query). Newest last.
    public List<JsonObject> Inbox { get; } = new();

    public void Note(string msg) => History.Add($"{DateTime.UtcNow:o} {msg}");

    public void AddToInbox(IEnumerable<JsonObject>? resources)
    {
        foreach (var res in resources ?? Enumerable.Empty<JsonObject>())
        {
            var clean = new JsonObject();
            foreach (var kv in res)
                if (!kv.Key.StartsWith("_")) clean[kv.Key] = kv.Value?.DeepClone();
            Inbox.Add(clean);
        }
    }

    public JsonObject ToJson()
    {
        var hist = new JsonArray();
        foreach (var h in History) hist.Add(h);
        var inbox = new JsonArray();
        foreach (var r in Inbox) inbox.Add(JsonX.Clone(r));
        return new JsonObject
        {
            ["referral_id"] = ReferralId,
            ["direct_message_id"] = DirectMessageId,
            ["submission_set_id"] = SubmissionSetId,
            ["sender_direct_address"] = SenderDirectAddress,
            ["recipient_direct_address"] = RecipientDirectAddress,
            ["task_id"] = TaskId,
            ["service_request_id"] = ServiceRequestId,
            ["status"] = Status,
            ["business_status"] = BusinessStatus,
            ["initiated_by"] = InitiatedBy,
            ["created"] = Created,
            ["history"] = hist,
            ["inbox"] = inbox,
        };
    }
}

/// <summary>In-memory episode store. Subclass for a persistent store. Mirrors stores.CorrelationStore.</summary>
public class CorrelationStore
{
    private readonly Dictionary<string, Episode> _byReferral = new();

    public Episode Create(Episode ep) { _byReferral[ep.ReferralId] = ep; return ep; }
    public Episode? Get(string referralId) => _byReferral.TryGetValue(referralId, out var e) ? e : null;
    public IReadOnlyList<JsonObject> All() => _byReferral.Values.Select(e => e.ToJson()).ToList();
}

/// <summary>Direct address &lt;-&gt; ODE Native endpoint. Mirrors stores.Directory.</summary>
public class Directory
{
    private readonly Dictionary<string, string> _directToFhir;
    public Directory(Dictionary<string, string>? mappings = null) => _directToFhir = mappings ?? new();

    public string FhirEndpointFor(string directAddress) =>
        _directToFhir.TryGetValue(directAddress, out var v) ? v : Config.Settings.OdeNativeBaseUrl;
}
