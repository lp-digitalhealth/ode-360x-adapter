namespace Ode.Adapter;

/// <summary>A parsed-enough 360X v2 message. Mirrors hl7v2.V2Message.</summary>
public sealed class V2Message
{
    public string MessageType { get; set; } = "";
    public string ControlId { get; set; } = "";
    public string? ReferralId { get; set; }
    public string? OrderStatus { get; set; }
    // Reply content parsed off the degraded 360X side (accepting provider, period end,
    // status reason, note, appointment start/end). Keys mirror hl7v2.py's V2Message.fields.
    public Dictionary<string, string> Fields { get; } = new();
    public string Raw { get; set; } = "";
}

/// <summary>Minimal HL7 v2 handling for the 360X workflow envelope. Mirrors hl7v2.py.</summary>
public static class Hl7v2
{
    /// <summary>Transaction -> v2 message type (validate against 360X Vol. 2).</summary>
    public static readonly IReadOnlyDictionary<string, string> TxMessageType =
        new Dictionary<string, string>
        {
            ["PCC-55"] = "OMG^O19",
            ["PCC-56"] = "OSU^O51",
            ["PCC-57"] = "OMG^O19",
            ["PCC-58"] = "OSU^O51",
            ["PCC-59"] = "OMG^O19",
            ["PCC-60"] = "SIU^S12",
            ["PCC-61"] = "SIU^S26",
        };

    private static string Ts() => DateTime.UtcNow.ToString("yyyyMMddHHmmss");

    public static V2Message Parse(string raw)
    {
        var msg = new V2Message { Raw = raw };
        var segs = raw.Replace("\n", "\r").Split('\r', StringSplitOptions.RemoveEmptyEntries);
        foreach (var seg in segs)
        {
            if (seg.Trim().Length == 0) continue;
            var f = seg.Split('|');
            var sid = f[0];
            if (sid == "MSH")
            {
                msg.MessageType = f.Length > 8 ? f[8] : "";
                msg.ControlId = f.Length > 9 ? f[9] : "";
            }
            else if (sid == "ORC")
            {
                // ORC-2 placer order number = referral id; ORC-5 order status.
                // Reply content: ORC-12 accepting provider, ORC-15 expected-by date,
                // ORC-16 coded status reason.
                if (f.Length > 2 && f[2].Length > 0) msg.ReferralId = f[2];
                if (f.Length > 5 && f[5].Length > 0) msg.OrderStatus = f[5];
                if (f.Length > 12 && f[12].Length > 0) msg.Fields["accepting_provider"] = f[12];
                if (f.Length > 15 && f[15].Length > 0) msg.Fields["period_end"] = f[15];
                if (f.Length > 16 && f[16].Length > 0) msg.Fields["status_reason"] = f[16];
            }
            else if (sid == "NTE")
            {
                if (f.Length > 3 && f[3].Length > 0) msg.Fields["note"] = f[3];
            }
            else if (sid == "ZRF" && f.Length > 1)
            {
                msg.ReferralId ??= f[1];
            }
            else if (sid == "SCH")
            {
                if (f.Length > 11 && f[11].Length > 0) msg.Fields["appointment_start"] = f[11];
                if (f.Length > 12 && f[12].Length > 0) msg.Fields["appointment_end"] = f[12];
            }
        }
        return msg;
    }

    private static void SetIdx(List<string> seg, int idx, string value)
    {
        while (seg.Count <= idx) seg.Add("");
        seg[idx] = value;
    }

    public static string Build(string messageType, string referralId,
                               string? orderStatus = null, string? controlId = null,
                               string? appointmentStart = null, string? appointmentEnd = null,
                               string? note = null, string? acceptingProvider = null,
                               string? statusReason = null, string? periodEnd = null)
    {
        controlId ??= $"ADP{Ts()}";
        var msh = string.Join("|", new[]
        {
            "MSH", "^~\\&", "OHIA-360ODE-ADAPTER", "OHIA",
            "EHR", "ORG", Ts(), "", messageType, controlId, "P", "2.5.1",
        });
        var segs = new List<string> { msh };
        var orderControl = messageType.StartsWith("OSU") ? "SC" : "NW";
        var orc = new List<string> { "ORC", orderControl, referralId, "", "", orderStatus ?? "" };
        if (!string.IsNullOrEmpty(acceptingProvider)) SetIdx(orc, 12, acceptingProvider);
        if (!string.IsNullOrEmpty(periodEnd)) SetIdx(orc, 15, periodEnd);
        if (!string.IsNullOrEmpty(statusReason)) SetIdx(orc, 16, statusReason);
        segs.Add(string.Join("|", orc));
        if (messageType.StartsWith("SIU"))
        {
            var sch = new List<string> { "SCH" };
            for (int i = 0; i < 12; i++) sch.Add("");
            sch[11] = appointmentStart ?? "";
            sch[12] = appointmentEnd ?? "";
            segs.Add(string.Join("|", sch));
        }
        if (!string.IsNullOrEmpty(note)) segs.Add(string.Join("|", new[] { "NTE", "1", "", note }));
        return string.Join("\r", segs) + "\r";
    }
}
