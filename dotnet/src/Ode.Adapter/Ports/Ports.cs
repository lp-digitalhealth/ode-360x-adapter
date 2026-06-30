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

    /// <summary>Transition a Task (e.g. cancelled on inbound PCC-58).</summary>
    JsonObject UpdateTaskStatus(string taskId, string status, string? reason = null);

    JsonObject GetTask(string taskId);
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
