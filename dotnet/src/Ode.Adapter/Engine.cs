using System.Text.Json.Nodes;
using Ode.Adapter.Plugins;
using Ode.Adapter.Ports;

namespace Ode.Adapter;

/// <summary>Engine — orchestrates the three layers via the ports. Mirrors engine.Adapter.
/// Handler methods return a Dictionary&lt;string, object?&gt; with the same keys as the
/// Python reference, so behavior and tests line up across languages.</summary>
public sealed class Adapter
{
    public IFhirBackend Fhir { get; }
    public IIheCodec Codec { get; }
    public IIheOutboundTransport Outbound { get; }
    public CorrelationStore Store { get; }
    public Directory Directory { get; }

    public Adapter(IFhirBackend fhir, IIheCodec codec, IIheOutboundTransport outbound,
                   CorrelationStore? store = null, Directory? directory = null)
    {
        Fhir = fhir;
        Codec = codec;
        Outbound = outbound;
        Store = store ?? new CorrelationStore();
        Directory = directory ?? new Directory();
    }

    public static Adapter FromConfig()
    {
        BuiltinPlugins.RegisterAll();
        var fhir = Registry.CreateFhir(Config.Settings.FhirBackend, new FhirBackendOptions
        {
            BaseUrl = Config.Settings.OdeNativeBaseUrl,
            DryRun = Config.Settings.DryRun,
        });
        var codec = Registry.CreateCodec(Config.Settings.IheCodec);
        var outbound = Registry.CreateTransport(Config.Settings.IheTransport);
        return new Adapter(fhir, codec, outbound);
    }

    // ----------------------------- INBOUND ------------------------------- //
    public Dictionary<string, object?> HandleInbound(object raw)
    {
        var env = Codec.Unpack(raw);
        var v2 = env.Hl7v2.Length > 0 ? Hl7v2.Parse(env.Hl7v2) : null;
        var referralId = !string.IsNullOrEmpty(v2?.ReferralId) ? v2!.ReferralId! : env.SubmissionSetId;

        return env.Transaction switch
        {
            "PCC-55" => InboundReferralRequest(env, referralId),
            "PCC-58" => InboundCancellation(referralId),
            _ => throw new ArgumentException($"Unsupported inbound transaction: {env.Transaction}"),
        };
    }

    private Dictionary<string, object?> InboundReferralRequest(InboundEnvelope env, string referralId)
    {
        var cda = env.PrimaryCda();
        if (string.IsNullOrEmpty(cda))
            throw new ArgumentException("PCC-55 missing C-CDA Referral Note");

        var bundle = CcdaToFhir.TransformReferralNote(cda, referralId);
        StampProvenance(bundle);

        var result = Fhir.SubmitReferralBundle(bundle);
        var (taskId, srId) = IdsFromResponse(result);

        var ep = Store.Create(new Episode
        {
            ReferralId = referralId,
            DirectMessageId = env.DirectMessageId,
            SubmissionSetId = env.SubmissionSetId,
            SenderDirectAddress = env.SenderDirectAddress,
            RecipientDirectAddress = env.RecipientDirectAddress,
            TaskId = taskId,
            ServiceRequestId = srId,
            Status = "requested",
        });
        ep.Note($"PCC-55 received; Task {taskId} created (requested)");

        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["task_id"] = taskId,
            ["service_request_id"] = srId,
            ["bundle"] = bundle,
            ["ode_response"] = result,
        };
    }

    private Dictionary<string, object?> InboundCancellation(string referralId)
    {
        var ep = Store.Get(referralId);
        if (ep == null)
            return new Dictionary<string, object?>
            {
                ["referral_id"] = referralId,
                ["action"] = "cancellation_no_episode",
            };
        if (ep.TaskId != null)
            Fhir.UpdateTaskStatus(ep.TaskId, "cancelled",
                                  "Referral cancelled by initiator (PCC-58)");
        ep.Status = "cancelled";
        ep.Note("PCC-58 received; Task revoked (cancelled)");
        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["task_id"] = ep.TaskId,
            ["action"] = "task_cancelled",
        };
    }

    // ----------------------------- OUTBOUND ------------------------------ //
    public Dictionary<string, object?> HandleTaskEvent(JsonObject task,
        IList<JsonObject>? resultResources = null, JsonObject? patient = null, bool interim = false)
    {
        var referralId = ReferralIdFromTask(task);
        var ep = Store.Get(referralId);
        var status = JsonX.Str(task["status"]) ?? "";

        var decision = StateMachine.TaskTo360X(status, resultResources is { Count: > 0 }, interim);
        if (decision == null)
            return new Dictionary<string, object?>
            {
                ["referral_id"] = referralId,
                ["action"] = "no_outbound_for_status",
                ["status"] = status,
            };

        var documents = new List<XdmDocument>();
        var lossNotes = new List<string>();
        if (decision.NeedsDocument)
        {
            var (cda, notes) = FhirToCcda.BuildConsultationNote(
                patient, resultResources ?? new List<JsonObject>(), interim);
            lossNotes = notes;
            documents.Add(new XdmDocument($"{referralId}-{decision.Transaction}", "text/xml", cda));
        }

        var v2 = Hl7v2.Build(Hl7v2.TxMessageType[decision.Transaction], referralId, decision.OrderStatus);

        var env = new OutboundEnvelope
        {
            Transaction = decision.Transaction,
            SenderDirectAddress = ep?.RecipientDirectAddress ?? "dental@example.org",
            RecipientDirectAddress = ep?.SenderDirectAddress ?? "ehr@example.org",
            Hl7v2 = v2,
            Documents = documents,
        };

        var packaged = Codec.Pack(env);
        Outbound.Send(packaged);

        if (ep != null)
        {
            ep.Status = status;
            ep.Note($"{decision.Transaction} emitted (status={status})");
        }
        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["transaction"] = decision.Transaction,
            ["packaged"] = packaged,
            ["loss_notes"] = lossNotes,
        };
    }

    // ------------------------------ helpers ------------------------------- //
    private static void StampProvenance(JsonObject bundle)
    {
        var now = DateTime.UtcNow.ToString("o");
        foreach (var e in JsonX.Arr(bundle["entry"]) ?? new JsonArray())
        {
            var res = JsonX.Obj((e as JsonObject)?["resource"]);
            if (res != null && JsonX.Str(res["resourceType"]) == "Provenance")
                res["recorded"] = now;
        }
    }

    private static (string? taskId, string? srId) IdsFromResponse(JsonObject response)
    {
        string? taskId = null, srId = null;
        foreach (var e in JsonX.Arr(response["entry"]) ?? new JsonArray())
        {
            var res = JsonX.Obj((e as JsonObject)?["resource"]);
            var rtype = JsonX.Str(res?["resourceType"]);
            if (rtype == "Task") taskId = JsonX.Str(res?["id"]);
            else if (rtype == "ServiceRequest") srId = JsonX.Str(res?["id"]);
        }
        return (taskId, srId);
    }

    private static string ReferralIdFromTask(JsonObject task)
    {
        foreach (var ident in JsonX.Arr(task["identifier"]) ?? new JsonArray())
        {
            var io = ident as JsonObject;
            if (JsonX.Str(io?["system"]) == "urn:ohia:referral-id")
                return JsonX.Str(io?["value"]) ?? "unknown";
        }
        return JsonX.Str(task["id"]) ?? "unknown";
    }
}
