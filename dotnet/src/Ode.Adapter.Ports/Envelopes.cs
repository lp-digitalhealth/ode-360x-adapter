namespace Ode.Adapter.Ports;

// See spec/contract/ports.md for the normative shape.
public record Document(string Id, string MimeType, string Content);

public record InboundEnvelope(
    string DirectMessageId, string SubmissionSetId,
    string SenderDirectAddress, string RecipientDirectAddress,
    string Transaction, string Hl7v2, IList<Document> Documents);

public record OutboundEnvelope(
    string Transaction, string SenderDirectAddress, string RecipientDirectAddress,
    string Hl7v2, IList<Document> Documents);
