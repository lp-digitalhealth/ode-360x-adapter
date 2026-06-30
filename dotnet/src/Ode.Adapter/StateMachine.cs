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
}
