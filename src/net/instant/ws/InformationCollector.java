package net.instant.ws;

import net.instant.util.WeakIndex;
import org.java_websocket.WebSocket;
import org.java_websocket.drafts.Draft;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.handshake.HandshakeBuilder;
import org.java_websocket.handshake.Handshakedata;
import org.java_websocket.handshake.ServerHandshakeBuilder;

public class InformationCollector {

    private final InstantWebSocketServer parent;
    private final WeakIndex<Handshakedata, Datum> requests;
    private final WeakIndex<WebSocket, Datum> connections;

    public InformationCollector(InstantWebSocketServer parent) {
        this.parent = parent;
        this.requests = new WeakIndex<Handshakedata, Datum>();
        this.connections = new WeakIndex<WebSocket, Datum>();
    }

    public InstantWebSocketServer getParent() {
        return parent;
    }

    public synchronized Datum addRequestLine(Handshakedata handshake,
                                             String line) {
        Datum d = new Datum(parent);
        d.initRequestLine(line);
        requests.put(handshake, d);
        return d;
    }
    public synchronized Datum addRequestData(WebSocket conn, Draft draft,
                                             ClientHandshake request) {
        Datum d = requests.get(request);
        d.initRequest(conn, draft, request);
        connections.put(conn, d);
        return d;
    }
    public synchronized Datum addResponse(ClientHandshake request,
                                          ServerHandshakeBuilder response,
                                          HandshakeBuilder result) {
        Datum d = requests.remove(request);
        d.initResponse((ServerHandshakeBuilder) result);
        return d;
    }
    public void postProcess(Datum d) {
        d.postProcess();
    }

    public synchronized Datum get(WebSocket ws) {
        return connections.get(ws);
    }

}
