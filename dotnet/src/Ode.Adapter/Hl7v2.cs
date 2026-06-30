namespace Ode.Adapter;

/// <summary>A parsed-enough 360X v2 message. Mirrors hl7v2.V2Message.</summary>
public sealed class V2Message
{
    public string MessageType { get; set; } = "";
    public string ControlId { get; set; } = "";
    public string? ReferralId { get; set; }
    public string? OrderStatus { get; set; }
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
                if (f.Length > 2 && f[2].Length > 0) msg.ReferralId = f[2];
                if (f.Length > 5 && f[5].Length > 0) msg.OrderStatus = f[5];
            }
            else if (sid == "ZRF" && f.Length > 1)
            {
                msg.ReferralId ??= f[1];
            }
        }
        return msg;
    }

    public static string Build(string messageType, string referralId,
                               string? orderStatus = null, string? controlId = null)
    {
        controlId ??= $"ADP{Ts()}";
        var msh = string.Join("|", new[]
        {
            "MSH", "^~\\&", "OHIA-360ODE-ADAPTER", "OHIA",
            "EHR", "ORG", Ts(), "", messageType, controlId, "P", "2.5.1",
        });
        var orderControl = messageType.StartsWith("OSU") ? "SC" : "NW";
        var orc = string.Join("|", new[] { "ORC", orderControl, referralId, "", "", orderStatus ?? "" });
        return msh + "\r" + orc + "\r";
    }
}
