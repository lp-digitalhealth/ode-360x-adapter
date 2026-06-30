namespace Ode.Adapter.Ports;

// Packaging port: wire bytes/dict <-> envelope (XDM, XDR, JSON).
public interface IIheCodec
{
    InboundEnvelope Unpack(object raw);
    object Pack(OutboundEnvelope envelope);
}
