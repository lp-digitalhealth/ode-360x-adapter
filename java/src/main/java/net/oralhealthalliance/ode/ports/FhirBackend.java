package net.oralhealthalliance.ode.ports;

import java.util.List;
import java.util.Map;

/**
 * Drives an ODE Native FHIR R4 server. One implementation per server flavor.
 * See spec/contract/ports.md (the language-neutral contract).
 */
public interface FhirBackend {
    /** Persist a transaction Bundle; return a transaction-response Bundle. */
    Map<String, Object> submitReferralBundle(Map<String, Object> bundle);

    /** Transition a Task (e.g. cancelled on inbound PCC-58). */
    Map<String, Object> updateTaskStatus(String taskId, String status, String reason);

    Map<String, Object> getTask(String taskId);

    /** Optional: result resources for outbound documents. */
    default List<Map<String, Object>> fetchResults(String taskId) { return List.of(); }
}
