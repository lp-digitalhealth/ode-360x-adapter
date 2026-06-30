package net.oralhealthalliance.ode.ports;

/** Packaging port: wire bytes/dict <-> envelope (XDM, XDR, JSON). */
public interface IheCodec {
    InboundEnvelope unpack(Object raw);
    Object pack(OutboundEnvelope envelope);
}
