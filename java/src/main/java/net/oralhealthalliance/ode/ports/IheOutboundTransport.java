package net.oralhealthalliance.ode.ports;

/** Sends a packaged outbound 360X message to the medical side. */
public interface IheOutboundTransport {
    Object send(Object packaged);
}
