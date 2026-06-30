namespace Ode.Adapter.Ports;

// Sends a packaged outbound 360X message to the medical side.
public interface IIheOutboundTransport
{
    object Send(object packaged);
}
