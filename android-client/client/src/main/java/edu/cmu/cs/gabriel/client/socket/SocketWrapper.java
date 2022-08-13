package edu.cmu.cs.gabriel.client.socket;

import android.app.Application;
import android.net.Network;
import android.security.NetworkSecurityPolicy;
import android.util.Log;

import androidx.annotation.NonNull;

import com.tinder.scarlet.Lifecycle;
import com.tinder.scarlet.Scarlet;
import com.tinder.scarlet.lifecycle.android.AndroidLifecycle;
import com.tinder.scarlet.websocket.okhttp.OkHttpClientUtils;

import java.security.KeyManagementException;
import java.security.KeyStoreException;
import java.security.NoSuchAlgorithmException;
import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.EventObserver;
import edu.cmu.cs.gabriel.client.observer.ResultObserver;
import edu.cmu.cs.gabriel.client.results.ErrorType;
import edu.cmu.cs.gabriel.protocol.Protos.FromClient;

import okhttp3.HttpUrl;
import okhttp3.OkHttpClient;

public class SocketWrapper {
    private static final String TAG = "SocketWrapper";

    private EventObserver eventObserver;
    private GabrielSocket webSocketInterface;
    private Network network;

    public SocketWrapper(String endpoint, int port, Application application, final Consumer<ErrorType> onDisconnect, ResultObserver resultObserver) {
        this(endpoint, port, application, onDisconnect, resultObserver, new SocketFactoryTcpNoDelay());
    }

    public SocketWrapper(String endpoint, int port, Application application, final Consumer<ErrorType> onDisconnect, ResultObserver resultObserver, @NonNull Network network) {
        new SocketWrapper(endpoint, port, application, onDisconnect, resultObserver, new SocketFactoryTcpNoDelay(network));
    }

    private SocketWrapper(String endpoint, int port, Application application, final Consumer<ErrorType> onDisconnect, ResultObserver resultObserver, @NonNull SocketFactoryTcpNoDelay socketFactoryTcpNoDelay)
    {
        UriOutput uriOutput = formatURI(endpoint, port);
        String wsURL;
        switch (uriOutput.getOutputType()) {
            case CLEARTEXT_WITHOUT_PERMISSION:
                throw new RuntimeException(
                        "Manifest file or security config does not allow cleartext connections.");
            case INVALID:
                throw new RuntimeException("Invalid endpoint.");
            case VALID:
                wsURL = uriOutput.getOutput();
                break;
            default:
                throw new IllegalStateException("Unexpected value: " + uriOutput.getOutputType());
        }

        Runnable onErrorResult = new Runnable() {
            @Override
            public void run() {
                onDisconnect.accept(ErrorType.SERVER_ERROR);
                SocketWrapper.this.stop();
            }
        };
        resultObserver.setOnErrorResult(onErrorResult);

        this.eventObserver = new EventObserver(onDisconnect);
        Lifecycle androidLifecycle = AndroidLifecycle.ofApplicationForeground(application);

        OkHttpClient.Builder okHttpClientBuilder = new OkHttpClient.Builder();
        try {
            SSLSocketFactoryTcpNoDelay sSLSocketFactoryTcpNoDelay =
                    new SSLSocketFactoryTcpNoDelay();
            okHttpClientBuilder.sslSocketFactory(sSLSocketFactoryTcpNoDelay.getSslSocketFactory(),
                    sSLSocketFactoryTcpNoDelay.getTrustManager());
        } catch (NoSuchAlgorithmException | KeyStoreException | KeyManagementException e) {
            Log.e(TAG, "TLS Socket error", e);
        }

        okHttpClientBuilder.socketFactory(socketFactoryTcpNoDelay);
        OkHttpClient okClient = okHttpClientBuilder.build();
        this.webSocketInterface = (new Scarlet.Builder())
                .webSocketFactory(OkHttpClientUtils.newWebSocketFactory(okClient, wsURL))
                .lifecycle(androidLifecycle.combineWith(this.eventObserver.getLifecycleRegistry()))
                .build().create(GabrielSocket.class);
        this.webSocketInterface.receive().start(resultObserver);
        this.webSocketInterface.observeWebSocketEvent().start(this.eventObserver);
    }

    public boolean isRunning() {
        return this.eventObserver.isRunning();
    }

    public void send(FromClient fromClient) {
        this.webSocketInterface.send(fromClient.toByteArray());
    }

    public void stop() {
        this.eventObserver.stop();
    }

    /**
     * This is used to check the given URL is valid or not.
     * @param endpoint     (host|IP) or websocket url
     * @return true if URI is valid
     */
    public static boolean validUri(String endpoint, int port) {
        return formatURI(endpoint, port).getOutputType() == UriOutput.OutputType.VALID;
    }

    /**
     * Format URI with port
     *
     * @param endpoint     (host|IP) or websocket url
     * @return String url if valid, null otherwise.
     */
    private static UriOutput formatURI(String endpoint, int port) {
        if (endpoint.isEmpty()) {
            return new UriOutput(UriOutput.OutputType.INVALID);
        }

        // make sure there is a scheme before we try to parse this
        if (!endpoint.matches("^[a-zA-Z]+://.*$")) {
            endpoint = "ws://" + endpoint;
        }

        // okhttp3.HttpUrl can only parse URIs starting with http or https
        endpoint = endpoint.replaceFirst("^ws://", "http://");
        endpoint = endpoint.replaceFirst("^wss://", "https://");
        HttpUrl httpurl = HttpUrl.parse(endpoint);

        if (httpurl == null) {
            return new UriOutput(UriOutput.OutputType.INVALID);
        }

        if (!httpurl.isHttps() &&
                !NetworkSecurityPolicy.getInstance().isCleartextTrafficPermitted(httpurl.host())) {
            return new UriOutput(UriOutput.OutputType.CLEARTEXT_WITHOUT_PERMISSION);
        }

        try {
            httpurl = httpurl.newBuilder().port(port).build();
        } catch (IllegalArgumentException e) {
            return new UriOutput(UriOutput.OutputType.INVALID);
        }

        String output = httpurl.toString().replaceFirst(
                "^http://", "ws://");
        output = output.replaceFirst("^https://", "wss://");
        return new UriOutput(UriOutput.OutputType.VALID, output);
    }
}
