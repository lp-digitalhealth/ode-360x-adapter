using System.Text.Json.Nodes;

namespace Ode.Adapter.Ports;

/// <summary>One document inside an XDM package (e.g. a C-CDA). Mirrors xdm.XdmDocument.</summary>
public sealed class XdmDocument
{
    public string Id { get; set; } = "";
    public string MimeType { get; set; } = "text/xml";
    public string Content { get; set; } = "";

    public XdmDocument() { }
    public XdmDocument(string id, string mimeType, string content)
    {
        Id = id; MimeType = mimeType; Content = content;
    }

    public JsonObject ToJson() => new()
    {
        ["id"] = Id,
        ["mime_type"] = MimeType,
        ["content"] = Content,
    };

    public static XdmDocument FromJson(JsonObject o) => new(
        o["id"]?.GetValue<string>() ?? "",
        o["mime_type"]?.GetValue<string>() ?? "text/xml",
        o["content"]?.GetValue<string>() ?? "");
}

/// <summary>Result of unpacking an inbound XDM package. Mirrors xdm.InboundEnvelope.</summary>
public sealed class InboundEnvelope
{
    public string DirectMessageId { get; set; } = "";
    public string SubmissionSetId { get; set; } = "";
    public string SenderDirectAddress { get; set; } = "";
    public string RecipientDirectAddress { get; set; } = "";
    public string Transaction { get; set; } = "";
    public string Hl7v2 { get; set; } = "";
    public List<XdmDocument> Documents { get; set; } = new();

    public static InboundEnvelope FromJson(JsonObject d)
    {
        var env = new InboundEnvelope
        {
            DirectMessageId = d["direct_message_id"]?.GetValue<string>() ?? "",
            SubmissionSetId = d["submission_set_id"]?.GetValue<string>() ?? "",
            SenderDirectAddress = d["sender_direct_address"]?.GetValue<string>() ?? "",
            RecipientDirectAddress = d["recipient_direct_address"]?.GetValue<string>() ?? "",
            Transaction = d["transaction"]?.GetValue<string>() ?? "",
            Hl7v2 = d["hl7v2"]?.GetValue<string>() ?? "",
        };
        if (d["documents"] is JsonArray docs)
            foreach (var doc in docs)
                if (doc is JsonObject jo) env.Documents.Add(XdmDocument.FromJson(jo));
        return env;
    }

    /// <summary>The first XML (C-CDA) document, or null.</summary>
    public string? PrimaryCda()
    {
        foreach (var doc in Documents)
            if (doc.MimeType.Contains("xml")) return doc.Content;
        return null;
    }
}

/// <summary>An outbound 360X package. Mirrors xdm.OutboundEnvelope.</summary>
public sealed class OutboundEnvelope
{
    public string Transaction { get; set; } = "";
    public string SenderDirectAddress { get; set; } = "";
    public string RecipientDirectAddress { get; set; } = "";
    public string Hl7v2 { get; set; } = "";
    public List<XdmDocument> Documents { get; set; } = new();

    public JsonObject ToJson()
    {
        var docs = new JsonArray();
        foreach (var d in Documents) docs.Add(d.ToJson());
        return new JsonObject
        {
            ["transaction"] = Transaction,
            ["sender_direct_address"] = SenderDirectAddress,
            ["recipient_direct_address"] = RecipientDirectAddress,
            ["hl7v2"] = Hl7v2,
            ["documents"] = docs,
        };
    }
}
