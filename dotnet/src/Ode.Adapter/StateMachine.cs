namespace Ode.Adapter;

/// <summary>A 360X emission decision. Mirrors state_machine.OutboundDecision.</summary>
public sealed record OutboundDecision(
    string Transaction, string? OrderStatus, bool NeedsDocument, string? DocumentType);

/// <summary>Layer 2 — 360X transaction &lt;-&gt; ODE Task state. Mirrors state_machine.py.</summary>
public static class StateMachine
{
    public static readonly IReadOnlyDictionary<string, string> InboundTxToTaskStatus =
        new Dictionary<string, string>
        {
            ["PCC-55"] = "requested",
            ["PCC-58"] = "cancelled",
        };

    /// <summary>Given an ODE Task status change, decide which 360X transaction to emit (or null).</summary>
    public static OutboundDecision? TaskTo360X(string taskStatus, bool hasResult = false, bool interim = false)
    {
        if ((taskStatus == "accepted" || taskStatus == "in-progress") && interim)
            return new OutboundDecision("PCC-59", null, true, Config.DocConsultationNote);
        if (taskStatus == "accepted" || taskStatus == "in-progress")
            return new OutboundDecision("PCC-56", "IP", false, null);   // accept
        if (taskStatus == "rejected" || taskStatus == "failed")
            return new OutboundDecision("PCC-56", "CA", false, null);   // decline
        if (taskStatus == "cancelled")
            return new OutboundDecision("PCC-58", "CA", false, null);
        if (taskStatus == "completed")
            return new OutboundDecision("PCC-57", "CM", true, Config.DocConsultationNote);
        return null;
    }

    public static OutboundDecision AppointmentEvent(bool noShow = false) =>
        noShow
            ? new OutboundDecision("PCC-61", null, false, null)
            : new OutboundDecision("PCC-60", null, false, null);

    // ----------------------------------------------------------------------- //
    // Reply ingestion (360X -> COW/FHIR) — the mirror direction. Used whenever
    // *this* side initiated the referral and the peer's replies arrive inbound.
    // ----------------------------------------------------------------------- //
    /// <summary>An inbound reply projected onto the COW Task/Request state.</summary>
    public sealed record ReplyDecision(
        string TaskStatus, string BusinessStatus, string? RequestStatus,
        bool NeedsDocument, string Kind);

    public static readonly IReadOnlySet<string> ReplyTransactions =
        new HashSet<string> { "PCC-56", "PCC-57", "PCC-59", "PCC-60", "PCC-61" };

    /// <summary>Map an inbound reply 360X transaction to a COW Task/Request state change.</summary>
    public static ReplyDecision? ReplyToFhir(string transaction, string? orderStatus = null)
    {
        switch (transaction)
        {
            case "PCC-56":
                return (orderStatus ?? "").ToUpperInvariant() == "CA"
                    ? new ReplyDecision("rejected", "declined", "revoked", false, "status")
                    : new ReplyDecision("accepted", "accepted", "active", false, "status");
            case "PCC-57":
                return new ReplyDecision("completed", "outcome-final", "completed", true, "outcome");
            case "PCC-59":
                return new ReplyDecision("in-progress", "interim-results", "active", true, "interim");
            case "PCC-60":
                return new ReplyDecision("in-progress", "appointment-booked", "active", false, "appointment");
            case "PCC-61":
                return new ReplyDecision("in-progress", "appointment-noshow", "active", false, "noshow");
            default:
                return null;
        }
    }
}
