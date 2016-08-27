package net.instant.api;

import java.net.InetSocketAddress;
import java.util.List;
import java.util.Map;

/**
 * Generic information about an incoming HTTP request.
 * The fields follow roughly the Apache Combined Log Format.
 */
public interface RequestData {

    /**
     * The address the request originated from.
     */
    InetSocketAddress getAddress();

    /**
     * The RFC 1413 identity of the remote peer.
     * Most probably not available and thus null.
     */
    String getRFC1413Identity();

    /**
     * The authenticated identity of the request.
     * E.g. an account name.
     * Not implemented by the backend, but possibly by plugins.
     * null if not available (e.g. for an anonymous user).
     */
    String getAuthIdentity();

    /**
     * The UNIX time at which the request arrived.
     */
    long getTimestamp();

    /**
     * The HTTP request line.
     * Since standard-abiding lines should be trivial to parse, a more
     * detailed view is not provided to reduce the interface size.
     * E.g. "GET / HTTP/1.1".
     */
    String getRequestLine();

    /**
     * The value of the HTTP Referer header.
     * The mis-spelling of the header name has been perpetuated by the
     * corresponding standard.
     */
    String getReferrer();

    /**
     * The value of the HTTP User-Agent header.
     */
    String getUserAgent();

    /**
     * A read-only mapping of HTTP request headers.
     * Repeated headers are not supported.
     */
    Map<String, String> getHeaders();

    /**
     * Convenience function to obtain a single HTTP header.
     */
    String getHeader(String name);

    /**
     * Return a list of all cookies submitted with the request.
     * Since the Cookie HTTP header does only transmit key-value pairs,
     * metadata may have to be amended when sending the cookie back.
     */
    List<Cookie> getCookies();

    /**
     * Additional meta-data about the request.
     * Not being expressed by the interface, the mapping maintains insertion
     * order and is read-write.
     */
    Map<String, Object> getExtraData();

}