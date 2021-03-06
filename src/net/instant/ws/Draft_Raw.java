package net.instant.ws;

import java.nio.ByteBuffer;
import java.util.Collections;
import java.util.List;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.java_websocket.WebSocketImpl;
import org.java_websocket.drafts.Draft;
import org.java_websocket.enums.CloseHandshakeType;
import org.java_websocket.enums.HandshakeState;
import org.java_websocket.exceptions.InvalidDataException;
import org.java_websocket.exceptions.NotSendableException;
import org.java_websocket.framing.BinaryFrame;
import org.java_websocket.framing.CloseFrame;
import org.java_websocket.framing.Framedata;
import org.java_websocket.framing.TextFrame;
import org.java_websocket.handshake.ClientHandshake;
import org.java_websocket.handshake.ClientHandshakeBuilder;
import org.java_websocket.handshake.HandshakeBuilder;
import org.java_websocket.handshake.ServerHandshake;
import org.java_websocket.handshake.ServerHandshakeBuilder;
import org.java_websocket.util.Charsetfunctions;

/**
 * A hack implementing raw HTTP transfers.
 */
public class Draft_Raw extends Draft {

    private static final Logger LOGGER = Logger.getLogger("Draft_Raw");

    @Override
    public HandshakeState acceptHandshakeAsClient(ClientHandshake request, ServerHandshake response) {
        /* Explicitly disallow WebSockets. */
        if (request.hasFieldValue("Sec-WebSocket-Key") || request.hasFieldValue("Sec-WebSocket-Accept"))
            return HandshakeState.NOT_MATCHED;
        return HandshakeState.MATCHED;
    }

    @Override
    public HandshakeState acceptHandshakeAsServer(ClientHandshake handshakedata) {
        /* Still explicitly disallow WebSockets. */
        boolean r = handshakedata.hasFieldValue("Sec-WebSocket-Version");
        return (r) ? HandshakeState.NOT_MATCHED : HandshakeState.MATCHED;
    }

    @Override
    public ByteBuffer createBinaryFrame(Framedata framedata) {
        /* Text/binary are passed through; others are discarded. */
        switch (framedata.getOpcode()) {
            case TEXT: case BINARY: break;
            default: return ByteBuffer.allocate(0);
        }
        ByteBuffer src = framedata.getPayloadData();
        ByteBuffer nbuf = ByteBuffer.allocate(src.limit());
        nbuf.put(src);
        nbuf.flip();
        return nbuf;
    }

    @Override
    public List<Framedata> createFrames(ByteBuffer binary, boolean mask) {
        /* From Fraft_6455.java */
        BinaryFrame curframe = new BinaryFrame();
        curframe.setPayload(binary);
        curframe.setTransferemasked(mask);
        try {
            curframe.isValid();
        } catch (InvalidDataException e) {
            throw new NotSendableException(e);
        }
        return Collections.singletonList((Framedata) curframe);
    }

    @Override
    public List<Framedata> createFrames(String text, boolean mask) {
        /* From Fraft_6455.java */
        TextFrame curframe = new TextFrame();
        curframe.setPayload(ByteBuffer.wrap(Charsetfunctions.utf8Bytes(text)));
        curframe.setTransferemasked(mask);
        try {
            curframe.isValid();
        } catch (InvalidDataException e) {
            throw new NotSendableException(e);
        }
        return Collections.singletonList((Framedata) curframe);
    }

    @Override
    public void processFrame(WebSocketImpl webSocketImpl, Framedata frame) throws InvalidDataException {
        /* FIXME: This should probably handle realistic frame sequences which wouldn't be generated by this class. */
        switch (frame.getOpcode()) {
            case TEXT:
                try {
                    webSocketImpl.getWebSocketListener().onWebsocketMessage(webSocketImpl, Charsetfunctions.stringUtf8(frame.getPayloadData()));
                } catch (RuntimeException exc) {
                    LOGGER.log(Level.SEVERE, "Exception while processing (fake) textual WebSocket frame:", exc);
                }
                break;
            case BINARY:
                try {
                    webSocketImpl.getWebSocketListener().onWebsocketMessage(webSocketImpl, frame.getPayloadData());
                } catch (RuntimeException exc) {
                    LOGGER.log(Level.SEVERE, "Exception while processing (fake) binary WebSocket frame:", exc);
                }
                break;
            default:
                throw new InvalidDataException(CloseFrame.REFUSE, "Unexpected frame with opcode " + frame.getOpcode());
        }
    }

    @Override
    public HandshakeBuilder postProcessHandshakeResponseAsServer(ClientHandshake request, ServerHandshakeBuilder response) {
        response.setHttpStatus((short) 404);
        response.setHttpStatusMessage("Not Found");
        response.put("Connection", "close");
        return response;
    }

    @Override
    public ClientHandshakeBuilder postProcessHandshakeRequestAsClient(ClientHandshakeBuilder request) {
        request.put("Connection", "close");
        return request;
    }

    @Override
    public void reset() {
        /* NOP */
    }

    @Override
    public List<Framedata> translateFrame(ByteBuffer buffer) {
        return createFrames(buffer, false);
    }

    @Override
    public CloseHandshakeType getCloseHandshakeType() {
        return CloseHandshakeType.NONE;
    }

    @Override
    public Draft copyInstance() {
        return new Draft_Raw();
    }

}
