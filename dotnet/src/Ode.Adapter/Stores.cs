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
    public string Created { get; } = DateTime.UtcNow.ToString("o");
    public List<string> History { get; } = new();

    public void Note(string msg) => History.Add($"{DateTime.UtcNow:o} {msg}");

    public JsonObject ToJson()
    {
        var hist = new JsonArray();
        foreach (var h in History) hist.Add(h);
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
            ["created"] = Created,
            ["history"] = hist,
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
