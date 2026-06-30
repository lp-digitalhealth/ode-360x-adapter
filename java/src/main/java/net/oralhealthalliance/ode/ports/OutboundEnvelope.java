package net.oralhealthalliance.ode.ports;

import java.util.List;

public record OutboundEnvelope(
    String transaction, String senderDirectAddress, String recipientDirectAddress,
    String hl7v2, List<InboundEnvelope.Document> documents) {}
