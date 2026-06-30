using System.Text;
using System.Text.Json.Nodes;
using Ode.Adapter.Ports;

namespace Ode.Adapter.Plugins;

/// <summary>JSON-envelope codec — the default, dependency-free codec.
/// Mirrors plugins/ihe/json_envelope.JsonEnvelopeCodec.</summary>
public sealed class JsonEnvelopeCodec : IIheCodec
{
    public string Name => "json-envelope";

    public InboundEnvelope Unpack(object raw)
    {
        JsonObject o = raw switch
        {
            JsonObject j => j,
            string s => (JsonObject)JsonNode.Parse(s)!,
            byte[] b => (JsonObject)JsonNode.Parse(Encoding.UTF8.GetString(b))!,
            _ => throw new ArgumentException($"unsupported raw envelope type: {raw.GetType().Name}"),
        };
        return InboundEnvelope.FromJson(o);
    }

    public object Pack(OutboundEnvelope envelope) => envelope.ToJson();
}

/// <summary>Records outbound messages instead of sending (reference/testing).
/// Mirrors plugins/ihe/json_envelope.CaptureTransport.</summary>
public sealed class CaptureTransport : IIheOutboundTransport
{
    public string Name => "capture";
    public List<object> Sent { get; } = new();

    public object Send(object packaged)
    {
        Sent.Add(packaged);
        return packaged;
    }
}

/// <summary>HTTP outbound transport — POSTs the packaged 360X message to a receiver URL.
/// Mirrors plugins/ihe/http_transport.HttpTransport.</summary>
public sealed class HttpTransport : IIheOutboundTransport
{
    private static readonly HttpClient Http = new();
    public string Name => "http";
    private readonly string _url;

    public HttpTransport(string? url = null) => _url = url ?? Config.Settings.IheOutboundUrl;

    public object Send(object packaged)
    {
        var json = (packaged as JsonNode)?.ToJsonString() ?? "{}";
        var req = new HttpRequestMessage(HttpMethod.Post, _url)
        {
            Content = new StringContent(json, Encoding.UTF8, "application/json")
        };
        var resp = Http.Send(req);
        resp.EnsureSuccessStatusCode();
        var text = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
        return string.IsNullOrEmpty(text)
            ? new JsonObject { ["status"] = (int)resp.StatusCode }
            : JsonNode.Parse(text)!;
    }
}

/// <summary>XDM ZIP codec — scaffold for real XDM. Mirrors plugins/ihe/xdm_zip.py.</summary>
public sealed class XdmZipCodec : IIheCodec
{
    public string Name => "xdm-zip";
    public InboundEnvelope Unpack(object raw) =>
        throw new NotImplementedException("XDM ZIP unpack not implemented — see spec/TODO.");
    public object Pack(OutboundEnvelope envelope) =>
        throw new NotImplementedException("XDM ZIP pack not implemented — see spec/TODO.");
}

/// <summary>Direct/SMTP outbound transport — scaffold. Mirrors plugins/ihe/direct_smtp.py.</summary>
public sealed class DirectTransport : IIheOutboundTransport
{
    public string Name => "direct";
    public object Send(object packaged) =>
        throw new NotImplementedException("Direct/SMTP send not implemented — S/MIME + HISP + MDN.");
}

/// <summary>Registers the built-in plugins. Mirrors plugins/__init__.py auto-registration.</summary>
public static class BuiltinPlugins
{
    private static bool _done;

    public static void RegisterAll()
    {
        if (_done) return;
        _done = true;
        Registry.RegisterFhir("generic-r4", o => new GenericR4Backend(o));
        Registry.RegisterFhir("onyx", o => new OnyxBackend(o));
        Registry.RegisterCodec("json-envelope", () => new JsonEnvelopeCodec());
        Registry.RegisterCodec("xdm-zip", () => new XdmZipCodec());
        Registry.RegisterTransport("capture", () => new CaptureTransport());
        Registry.RegisterTransport("direct", () => new DirectTransport());
        Registry.RegisterTransport("http", () => new HttpTransport());
    }
}
