package edu.cmu.cs.gabriel.client.okhttp;

import java.util.Random;

import okhttp3.OkHttpClient;
import okhttp3.Request;
import okhttp3.WebSocket;
import okhttp3.WebSocketListener;

public class GabrielOkHttpClient extends OkHttpClient {
    @Override
    public WebSocket newWebSocket(Request request, WebSocketListener listener) {
        GabrielWebSocket webSocket = new GabrielWebSocket(
                request, listener, new Random(), this.pingIntervalMillis());
        webSocket.connect(this);
        return webSocket;
    }
}
