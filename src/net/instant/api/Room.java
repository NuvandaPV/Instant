package net.instant.api;

import java.util.Set;

/**
 * Representation of a chat room.
 * Instances may or may not be garbage-collected when there are no clients
 * connected to a room.
 * A special (unique) instance is maintained for clients that are not members
 * of "proper" rooms. For it, getName() returns null; broadcasting to it
 * fails with an exception; see also RoomGroup for the behavior of the
 * methods declared there.
 */
public interface Room {

    /**
     * The name of the room.
     */
    String getName();

    /**
     * All clients currently connected to the room.
     */
    Set<ClientConnection> getClients();

    /**
     * Send a message to a single client.
     * Returns the ID of the message as filled in by the core.
     */
    String sendUnicast(ClientConnection client, MessageContents msg);

    /**
     * Send a message to all room members.
     * Returns the ID of the message as filled in by the core.
     */
    String sendBroadcast(MessageContents msg);

    /**
     * The (global) group the room belongs to.
     */
    RoomGroup getGroup();

    /**
     * Construct a new message body.
     * If makeID is true, the message is assigned a new unique ID; the given
     * type is assigned to the corresponding field.
     */
    MessageContents makeMessage(boolean makeID, String type);

}
