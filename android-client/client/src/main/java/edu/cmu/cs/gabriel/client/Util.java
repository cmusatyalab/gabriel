package edu.cmu.cs.gabriel.client;

import java.net.URI;

import androidx.annotation.Nullable;
import okhttp3.HttpUrl;

public class Util {
    /**
     * This is used to check the given URL is valid or not.
     *
     * @param endpoint     (host|IP)[:port] or websocket url
     * @param default_port
     * @return String url if valid, null otherwise.
     */
    @Nullable
    public static String ValidateEndpoint(String endpoint, int default_port) {

        if (endpoint.isEmpty())
            return null;

        // make sure there is a scheme before we try to parse this
        if (!endpoint.matches("^[a-zA-Z]+://.*$")) {
            endpoint = "ws://" + endpoint;
        }

        // okhttp3.HttpUrl can only parse URIs starting with http or https
        endpoint = endpoint.replaceFirst("^ws://", "http://").replaceFirst("^wss://", "https://");
        HttpUrl httpurl = HttpUrl.parse(endpoint);

        if (httpurl == null)
            return null;

        // set our default port if it hadn't been set in the original endpoint string
        try {
            if (URI.create(endpoint).getPort() == -1) {
                httpurl = httpurl.newBuilder().port(default_port).build();
            }
        } catch (IllegalArgumentException e) {
            return null;
        }

        return httpurl.toString().replaceFirst("^http", "ws");
    }
}
