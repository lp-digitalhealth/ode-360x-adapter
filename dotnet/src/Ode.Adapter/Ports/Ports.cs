using System.Text.Json.Nodes;

namespace Ode.Adapter.Ports;

// The three ports the mapping core depends on. Concrete plugins implement them;
// the engine never references a concrete server or transport. See spec/contract/ports.md.

/// <summary>Drives an ODE Native FHIR R4 server. One implementation per server flavor.</summary>
public interface IFhirBackend
{
    string Name { get; }

    /// <summary>Persist a transaction Bundle; return a transaction-response Bundle.</summary>
    JsonObject SubmitReferralBundle(JsonObject bundle);

    /// <summary>Transition a Task, carrying the reply content (businessStatus, output,
    /// owner, statusReason, note, restriction.period). Optional args in the same order
    /// as generic_r4.update_task_status.</summary>
    JsonObject UpdateTaskStatus(string taskId, string status, string? reason = null,
                                string? businessStatus = null, JsonArray? outputs = null,
                                JsonNode? owner = null, JsonObject? statusReason = null,
                                string? note = null, string? periodEnd = null);

    /// <summary>Transition the ServiceRequest lifecycle (e.g. completed / revoked).</summary>
    JsonObject UpdateRequestStatus(string requestId, string status, string? reason = null);

    JsonObject GetTask(string taskId);

    /// <summary>Live read: the resources the bridge wrote for a referral (harness inbox).
    /// Dry-run returns empty (the engine uses its per-episode cache).</summary>
    List<JsonObject> FindByReferral(string referralId) => new();
}

/// <summary>Packaging port: wire bytes/object &lt;-&gt; envelope (XDM, XDR, JSON).</summary>
public interface IIheCodec
{
    string Name { get; }
    InboundEnvelope Unpack(object raw);
    object Pack(OutboundEnvelope envelope);
}

/// <summary>Sends a packaged outbound 360X message to the medical side.</summary>
public interface IIheOutboundTransport
{
    string Name { get; }
    object Send(object packaged);
}
