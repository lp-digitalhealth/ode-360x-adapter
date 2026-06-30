package net.oralhealthalliance.ode.ports;

import java.util.List;

/** See spec/contract/ports.md for the normative shape. */
public record InboundEnvelope(
    String directMessageId, String submissionSetId,
    String senderDirectAddress, String recipientDirectAddress,
    String transaction, String hl7v2, List<Document> documents) {
    public record Document(String id, String mimeType, String content) {}
}
