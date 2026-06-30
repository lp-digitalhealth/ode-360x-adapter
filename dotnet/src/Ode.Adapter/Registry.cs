using Ode.Adapter.Ports;

namespace Ode.Adapter;

/// <summary>Options passed to a FHIR backend factory (mirrors the Python kwargs).</summary>
public sealed class FhirBackendOptions
{
    public string? BaseUrl { get; set; }
    public bool? DryRun { get; set; }
    public string LoadMode { get; set; } = "transaction";
}

/// <summary>Plugin registry — selects port implementations by name at runtime.
/// Mirrors registry.py. Built-ins register via BuiltinPlugins.RegisterAll().</summary>
public static class Registry
{
    private static readonly Dictionary<string, Func<FhirBackendOptions, IFhirBackend>> Fhir = new();
    private static readonly Dictionary<string, Func<IIheCodec>> Codecs = new();
    private static readonly Dictionary<string, Func<IIheOutboundTransport>> Transports = new();

    public static void RegisterFhir(string name, Func<FhirBackendOptions, IFhirBackend> f) => Fhir[name] = f;
    public static void RegisterCodec(string name, Func<IIheCodec> f) => Codecs[name] = f;
    public static void RegisterTransport(string name, Func<IIheOutboundTransport> f) => Transports[name] = f;

    public static IFhirBackend CreateFhir(string name, FhirBackendOptions? options = null) =>
        Fhir.TryGetValue(name, out var f)
            ? f(options ?? new FhirBackendOptions())
            : throw new KeyNotFoundException(
                $"no 'fhir' plugin named '{name}'. Available: {string.Join(", ", Available("fhir"))}");

    public static IIheCodec CreateCodec(string name) =>
        Codecs.TryGetValue(name, out var f) ? f()
            : throw new KeyNotFoundException(
                $"no 'codec' plugin named '{name}'. Available: {string.Join(", ", Available("codec"))}");

    public static IIheOutboundTransport CreateTransport(string name) =>
        Transports.TryGetValue(name, out var f) ? f()
            : throw new KeyNotFoundException(
                $"no 'transport' plugin named '{name}'. Available: {string.Join(", ", Available("transport"))}");

    public static IReadOnlyList<string> Available(string kind) => kind switch
    {
        "fhir" => Fhir.Keys.OrderBy(k => k).ToList(),
        "codec" => Codecs.Keys.OrderBy(k => k).ToList(),
        "transport" => Transports.Keys.OrderBy(k => k).ToList(),
        _ => throw new ArgumentException($"unknown plugin kind: {kind}"),
    };

    public static IReadOnlyDictionary<string, IReadOnlyList<string>> AllPlugins() =>
        new Dictionary<string, IReadOnlyList<string>>
        {
            ["fhir"] = Available("fhir"),
            ["codec"] = Available("codec"),
            ["transport"] = Available("transport"),
        };
}
