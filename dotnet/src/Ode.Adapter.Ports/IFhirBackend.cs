namespace Ode.Adapter.Ports;

// Drives an ODE Native FHIR R4 server. See spec/contract/ports.md.
public interface IFhirBackend
{
    // Persist a transaction Bundle; return a transaction-response Bundle.
    IDictionary<string, object> SubmitReferralBundle(IDictionary<string, object> bundle);

    // Transition a Task (e.g. cancelled on inbound PCC-58).
    IDictionary<string, object> UpdateTaskStatus(string taskId, string status, string? reason = null);

    IDictionary<string, object> GetTask(string taskId);

    // Optional: result resources for outbound documents.
    IList<IDictionary<string, object>> FetchResults(string taskId) => new List<IDictionary<string, object>>();
}
