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

        if (env.Transaction == "PCC-55") return InboundReferralRequest(env, referralId);
        if (env.Transaction == "PCC-58") return InboundCancellation(referralId);
        if (StateMachine.ReplyTransactions.Contains(env.Transaction)) return InboundReply(env, referralId, v2);
        throw new ArgumentException($"Unsupported inbound transaction: {env.Transaction}");
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
            BusinessStatus = "referral-sent",
            InitiatedBy = "medical",
        });
        ep.AddToInbox(Produced(bundle));
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
        ep.BusinessStatus = "cancelled";
        ep.Note("PCC-58 received; Task revoked (cancelled)");
        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["task_id"] = ep.TaskId,
            ["action"] = "task_cancelled",
        };
    }

    // -------------------- INBOUND: reply ingestion (mirror) --------------- //
    private Dictionary<string, object?> InboundReply(InboundEnvelope env, string referralId, V2Message? v2)
    {
        var tx = env.Transaction;
        var decision = StateMachine.ReplyToFhir(tx, v2?.OrderStatus);
        if (decision == null) throw new ArgumentException($"Unsupported reply transaction: {tx}");

        var ep = Store.Get(referralId) ?? OrphanEpisode(env, referralId);
        var taskRef = ep.TaskId != null ? $"Task/{ep.TaskId}" : null;
        var fields = v2?.Fields ?? new Dictionary<string, string>();

        var produced = new List<JsonObject>();
        JsonArray outputs = new();
        var cda = env.PrimaryCda();
        var noteText = fields.TryGetValue("note", out var n) ? n : null;
        string? ownerRef = null;
        JsonObject? statusReasonCc = null;
        string? periodEnd = null;

        if (decision.Kind is "outcome" or "interim")
        {
            if (string.IsNullOrEmpty(cda)) throw new ArgumentException($"{tx} missing C-CDA Consultation Note");
            var bundle = CcdaToFhir.TransformConsultationNote(cda, referralId, interim: decision.Kind == "interim");
            StampProvenance(bundle);
            Fhir.SubmitReferralBundle(bundle);
            produced = Produced(bundle);
            Cow.ApplyDentalProfiles(produced);
            var pref = PatientRef(produced);
            produced.Add(decision.Kind == "interim"
                ? Cow.InterimObservation(referralId, pref, noteText ?? "Interim finding")
                : Cow.ClearanceObservation(referralId, pref, noteText ?? "cleared"));
            outputs = Cow.TaskOutput(produced);
        }
        else if (decision.Kind == "appointment")
        {
            var appt = Cow.BuildAppointment(referralId, null, taskRef,
                fields.GetValueOrDefault("appointment_start"),
                end: fields.GetValueOrDefault("appointment_end"), location: noteText,
                provider: fields.GetValueOrDefault("accepting_provider"),
                apptType: fields.GetValueOrDefault("status_reason"));
            produced = new List<JsonObject> { appt };
            Fhir.SubmitReferralBundle(WrapBundle(produced));
            outputs = Cow.TaskOutput(produced);
        }
        else if (decision.Kind == "noshow")
        {
            var comm = Cow.BuildCommunication(referralId, null, taskRef,
                reason: "Patient did not attend the scheduled appointment (no-show).",
                reasonCode: fields.GetValueOrDefault("status_reason"), reschedule: noteText);
            produced = new List<JsonObject> { comm };
            Fhir.SubmitReferralBundle(WrapBundle(produced));
            outputs = Cow.TaskOutput(produced);
        }
        else if (decision.Kind == "status")
        {
            if (tx == "PCC-56" && decision.TaskStatus == "accepted")
            {
                var (ownerRes, oref) = Cow.BuildOwnerRole(ProviderFromV2(fields.GetValueOrDefault("accepting_provider")));
                ownerRef = oref;
                if (ownerRes.Count > 0)
                {
                    Fhir.SubmitReferralBundle(WrapBundle(ownerRes));
                    produced.AddRange(ownerRes);
                }
                periodEnd = IsoDate(fields.GetValueOrDefault("period_end"));
            }
            else if (tx == "PCC-56")   // decline
            {
                statusReasonCc = Cow.DeclineReasonConcept(fields.GetValueOrDefault("status_reason"));
            }
        }

        JsonObject taskSnapshot;
        var outArg = outputs.Count > 0 ? outputs : null;
        var ownerArg = ownerRef != null ? (JsonNode)JsonValue.Create(ownerRef) : null;
        if (ep.TaskId != null)
            taskSnapshot = Fhir.UpdateTaskStatus(ep.TaskId, decision.TaskStatus, reason: $"{tx} received",
                businessStatus: decision.BusinessStatus, outputs: outArg, owner: ownerArg,
                statusReason: statusReasonCc, note: noteText, periodEnd: periodEnd);
        else
            taskSnapshot = Cow.TaskSnapshot(referralId, null, decision.TaskStatus, decision.BusinessStatus,
                outputs: outArg, owner: ownerArg, statusReason: statusReasonCc, note: noteText, periodEnd: periodEnd);

        if ((decision.RequestStatus == "completed" || decision.RequestStatus == "revoked") && ep.ServiceRequestId != null)
            Fhir.UpdateRequestStatus(ep.ServiceRequestId, decision.RequestStatus, reason: $"{tx} received");

        ep.Status = decision.TaskStatus;
        ep.BusinessStatus = decision.BusinessStatus;
        ep.Note($"{tx} received; Task -> {decision.TaskStatus} ({decision.BusinessStatus})");
        var inbox = new List<JsonObject>(produced);
        if (taskSnapshot != null) inbox.Add(taskSnapshot);
        ep.AddToInbox(inbox);

        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["transaction"] = tx,
            ["task_status"] = decision.TaskStatus,
            ["business_status"] = decision.BusinessStatus,
            ["request_status"] = decision.RequestStatus,
            ["task"] = taskSnapshot,
            ["resources"] = produced,
            ["task_id"] = ep.TaskId,
        };
    }

    private Episode OrphanEpisode(InboundEnvelope env, string referralId)
    {
        var ep = Store.Create(new Episode
        {
            ReferralId = referralId,
            DirectMessageId = env.DirectMessageId,
            SubmissionSetId = env.SubmissionSetId,
            SenderDirectAddress = env.SenderDirectAddress,
            RecipientDirectAddress = env.RecipientDirectAddress,
            Status = "requested",
        });
        ep.Note("reply received without a tracked initiation; episode opened");
        return ep;
    }

    // ---------------- OUTBOUND: referral initiation (dental) -------------- //
    public Dictionary<string, object?> HandleReferralInitiation(string referralId, JsonObject rich,
        string? sender = null, string? recipient = null)
    {
        rich = JsonX.Clone(rich);
        rich["referral_id"] = referralId;
        var bundle = ReferralFhir.BuildReferralBundle(rich);
        StampProvenance(bundle);
        Fhir.SubmitReferralBundle(bundle);
        var produced = Produced(bundle);
        var (cda, lossNotes) = FhirToCcda.BuildReferralNote(rich);
        sender ??= JsonX.Str(rich["sender"]);
        recipient ??= JsonX.Str(rich["recipient"]);
        var srId = SrId(produced);

        var v2 = Hl7v2.Build("OMG^O19", referralId);
        var documents = new List<XdmDocument> { new($"{referralId}-PCC-55", "text/xml", cda) };
        var env = new OutboundEnvelope
        {
            Transaction = "PCC-55",
            SenderDirectAddress = sender ?? "intake@dentalgroup.direct.example.org",
            RecipientDirectAddress = recipient ?? "referrals@oncology.direct.example.org",
            Hl7v2 = v2,
            Documents = documents,
        };
        var packaged = Codec.Pack(env);
        Outbound.Send(packaged);

        var ep = Store.Get(referralId) ?? Store.Create(new Episode
        {
            ReferralId = referralId,
            DirectMessageId = $"<{referralId}@dental>",
            SubmissionSetId = $"urn:ohia:submissionset:{referralId}",
            SenderDirectAddress = env.SenderDirectAddress,
            RecipientDirectAddress = env.RecipientDirectAddress,
            ServiceRequestId = srId,
            Status = "requested",
            BusinessStatus = "referral-sent",
            InitiatedBy = "dental",
        });
        if (produced.Count > 0) ep.AddToInbox(produced);
        ep.Note("PCC-55 initiated (dental -> medical); referral sent");
        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["transaction"] = "PCC-55",
            ["packaged"] = packaged,
            ["loss_notes"] = lossNotes,
            ["bundle"] = bundle,
            ["resources"] = produced,
            ["service_request_id"] = srId,
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

        // Carry the reply content the fulfiller put on the Task into the degraded v2:
        // accepting provider (ORC-12), expected-by (ORC-15), decline reason (ORC-16), note (NTE).
        var v2 = Hl7v2.Build(Hl7v2.TxMessageType[decision.Transaction], referralId, decision.OrderStatus,
            note: TaskNote(task), acceptingProvider: TaskOwnerProvider(task),
            statusReason: TaskStatusReasonCode(task), periodEnd: TaskPeriodEnd(task));

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

        return FinishOutbound(ep, referralId, decision, packaged, status, lossNotes);
    }

    public Dictionary<string, object?> HandleAppointmentEvent(string referralId, bool noShow = false,
        string? appointmentStart = null, string? appointmentEnd = null, string? location = null,
        string? provider = null, string? apptType = null, string? reason = null, string? reschedule = null)
    {
        var ep = Store.Get(referralId);
        var decision = StateMachine.AppointmentEvent(noShow);
        string v2 = noShow
            ? Hl7v2.Build(Hl7v2.TxMessageType[decision.Transaction], referralId,
                note: reschedule ?? reason, statusReason: reason)
            : Hl7v2.Build(Hl7v2.TxMessageType[decision.Transaction], referralId,
                appointmentStart: appointmentStart, appointmentEnd: appointmentEnd,
                note: location, acceptingProvider: provider, statusReason: apptType);
        var env = new OutboundEnvelope
        {
            Transaction = decision.Transaction,
            SenderDirectAddress = ep?.RecipientDirectAddress ?? "dental@example.org",
            RecipientDirectAddress = ep?.SenderDirectAddress ?? "ehr@example.org",
            Hl7v2 = v2,
            Documents = new List<XdmDocument>(),
        };
        var packaged = Codec.Pack(env);
        Outbound.Send(packaged);
        return FinishOutbound(ep, referralId, decision, packaged, null, new List<string>());
    }

    // COW business-status applied when emitting each outbound reply (medical-initiated).
    private static readonly IReadOnlyDictionary<string, string> OutboundBusinessStatus =
        new Dictionary<string, string>
        {
            ["PCC-56"] = "accepted",   // decline overridden to "declined" at the call site
            ["PCC-59"] = "interim-results",
            ["PCC-57"] = "outcome-final",
            ["PCC-58"] = "cancelled",
            ["PCC-60"] = "appointment-booked",
            ["PCC-61"] = "appointment-noshow",
        };

    private Dictionary<string, object?> FinishOutbound(Episode? ep, string referralId,
        OutboundDecision decision, object packaged, string? status, List<string> lossNotes)
    {
        OutboundBusinessStatus.TryGetValue(decision.Transaction, out var business);
        if (decision.Transaction == "PCC-56" && decision.OrderStatus == "CA") business = "declined";
        if (ep != null)
        {
            if (!string.IsNullOrEmpty(status)) ep.Status = status;
            if (!string.IsNullOrEmpty(business)) ep.BusinessStatus = business;
            ep.Note($"{decision.Transaction} emitted (status={status}, businessStatus={business})");
        }
        return new Dictionary<string, object?>
        {
            ["referral_id"] = referralId,
            ["transaction"] = decision.Transaction,
            ["business_status"] = business,
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

    private static JsonObject WrapBundle(IEnumerable<JsonObject> resources)
    {
        var entries = new JsonArray();
        foreach (var res in resources)
        {
            var clean = new JsonObject();
            foreach (var kv in res) if (!kv.Key.StartsWith("_")) clean[kv.Key] = kv.Value?.DeepClone();
            entries.Add(new JsonObject
            {
                ["fullUrl"] = JsonX.Str(res["_fullUrl"]),
                ["resource"] = clean,
                ["request"] = new JsonObject { ["method"] = "POST", ["url"] = JsonX.Str(clean["resourceType"]) },
            });
        }
        return new JsonObject { ["resourceType"] = "Bundle", ["type"] = "transaction", ["entry"] = entries };
    }

    private static List<JsonObject> Produced(JsonObject bundle)
    {
        var outList = new List<JsonObject>();
        foreach (var e in JsonX.Arr(bundle["entry"]) ?? new JsonArray())
        {
            var res = JsonX.Obj((e as JsonObject)?["resource"]);
            if (res != null && JsonX.Str(res["resourceType"]) != "Provenance") outList.Add(res);
        }
        return outList;
    }

    private static string? SrId(IEnumerable<JsonObject> resources)
    {
        foreach (var res in resources)
            if (JsonX.Str(res["resourceType"]) == "ServiceRequest") return JsonX.Str(res["id"]);
        return null;
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

    private static string? PatientRef(IEnumerable<JsonObject> resources)
    {
        foreach (var res in resources)
            if (JsonX.Str(res["resourceType"]) == "Patient")
            {
                var fu = JsonX.Str(res["_fullUrl"]);
                if (!string.IsNullOrEmpty(fu)) return fu;
                var id = JsonX.Str(res["id"]);
                return id != null ? $"Patient/{id}" : null;
            }
        return null;
    }

    private static JsonObject? ProviderFromV2(string? field)
    {
        if (string.IsNullOrEmpty(field)) return null;
        var parts = field.Split('^');
        if (parts.Length >= 2 && parts[0].Length > 0)
        {
            var name = string.Join("^", parts.Skip(1)).Trim();
            var o = new JsonObject { ["npi"] = parts[0] };
            o["name"] = name.Length > 0 ? name : null;
            return o;
        }
        return new JsonObject { ["name"] = field };
    }

    private static string? IsoDate(string? v)
    {
        if (string.IsNullOrEmpty(v)) return null;
        v = v.Trim();
        if (v.Length >= 8 && v.All(char.IsDigit)) return $"{v[..4]}-{v.Substring(4, 2)}-{v.Substring(6, 2)}";
        return v;
    }

    private static string? TaskNote(JsonObject task)
    {
        var notes = JsonX.Arr(task["note"]);
        if (notes is { Count: > 0 }) return JsonX.Str((notes[0] as JsonObject)?["text"]);
        return null;
    }

    private static string? TaskOwnerProvider(JsonObject task)
    {
        var owner = JsonX.Obj(task["owner"]);
        if (owner == null) return null;
        var npi = "";
        foreach (var ident in JsonX.Arr(owner["identifier"]) ?? new JsonArray())
            if (JsonX.Str((ident as JsonObject)?["system"]) == "http://hl7.org/fhir/sid/us-npi")
                npi = JsonX.Str((ident as JsonObject)?["value"]) ?? "";
        var name = JsonX.Str(owner["display"]);
        if (!string.IsNullOrEmpty(npi) || !string.IsNullOrEmpty(name))
        {
            var s = $"{npi}^{name ?? ""}".Trim('^');
            return string.IsNullOrEmpty(s) ? null : s;
        }
        return null;
    }

    private static string? TaskStatusReasonCode(JsonObject task)
    {
        var sr = JsonX.Obj(task["statusReason"]);
        if (sr == null) return null;
        foreach (var c in JsonX.Arr(sr["coding"]) ?? new JsonArray())
        {
            var code = JsonX.Str((c as JsonObject)?["code"]);
            if (!string.IsNullOrEmpty(code)) return code;
        }
        return JsonX.Str(sr["text"]);
    }

    private static string? TaskPeriodEnd(JsonObject task)
    {
        var end = JsonX.Str(JsonX.Obj(JsonX.Obj(task["restriction"])?["period"])?["end"]);
        if (string.IsNullOrEmpty(end)) return null;
        var digits = end.Replace("-", "");
        var r = digits.Length > 8 ? digits[..8] : digits;
        return string.IsNullOrEmpty(r) ? null : r;
    }
}
