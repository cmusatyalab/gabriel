package edu.cmu.cs.gabriel.client.socket;

import com.tinder.scarlet.Stream;
import com.tinder.scarlet.WebSocket.Event;
import com.tinder.scarlet.ws.Receive;
import com.tinder.scarlet.ws.Send;

interface GabrielSocket {
    @Send
    void send(byte[] rawFromClient);

    @Receive
    Stream<byte[]> receive();

    @Receive
    Stream<Event> observeWebSocketEvent();
}