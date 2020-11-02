package edu.cmu.cs.gabriel.client.socket;

import java.io.IOException;
import java.net.InetAddress;
import java.net.Socket;
import java.security.KeyManagementException;
import java.security.KeyStore;
import java.security.KeyStoreException;
import java.security.NoSuchAlgorithmException;
import java.util.Arrays;

import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLSocketFactory;
import javax.net.ssl.TrustManager;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

public class SSLSocketFactoryTcpNoDelay extends SSLSocketFactory {
    private static final String TAG = "SSLSocketFactoryTCPNoDelay";

    final private X509TrustManager trustManager;
    final private SSLSocketFactory sslSocketFactory;

    public SSLSocketFactoryTcpNoDelay() throws NoSuchAlgorithmException, KeyStoreException,
            KeyManagementException {
        // From
        // https://square.github.io/okhttp/4.x/okhttp/okhttp3/-ok-http-client/-builder/ssl-socket-factory
        TrustManagerFactory trustManagerFactory = TrustManagerFactory.getInstance(
                TrustManagerFactory.getDefaultAlgorithm());
        trustManagerFactory.init((KeyStore) null);
        TrustManager[] trustManagers = trustManagerFactory.getTrustManagers();
        if (trustManagers.length != 1 || !(trustManagers[0] instanceof X509TrustManager)) {
            throw new IllegalStateException("Unexpected default trust managers:"
                    + Arrays.toString(trustManagers));
        }
        trustManager = (X509TrustManager) trustManagers[0];

        SSLContext sslContext = SSLContext.getInstance("TLS");
        sslContext.init(null, new TrustManager[] { trustManager }, null);
        sslSocketFactory = sslContext.getSocketFactory();
    }

    public X509TrustManager getTrustManager() {
        return trustManager;
    }

    public SSLSocketFactory getSslSocketFactory() {
        return sslSocketFactory;
    }

    @Override
    public String[] getDefaultCipherSuites() {
        return sslSocketFactory.getDefaultCipherSuites();
    }

    @Override
    public String[] getSupportedCipherSuites() {
        return sslSocketFactory.getSupportedCipherSuites();
    }

    @Override
    public Socket createSocket() throws IOException {
        Socket socket = sslSocketFactory.createSocket();
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(Socket s, String host, int port, boolean autoClose) throws
            IOException {
        Socket socket = sslSocketFactory.createSocket(s, host, port, autoClose);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(String host, int port) throws IOException {
        Socket socket = sslSocketFactory.createSocket(host, port);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(String host, int port, InetAddress localHost, int localPort) throws
            IOException {
        Socket socket = sslSocketFactory.createSocket(host, port, localHost, localPort);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(InetAddress host, int port) throws IOException {
        Socket socket = sslSocketFactory.createSocket(host, port);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(InetAddress address, int port, InetAddress localAddress,
                               int localPort) throws IOException {
        Socket socket = sslSocketFactory.createSocket(address, port, localAddress, localPort);
        socket.setTcpNoDelay(true);
        return socket;
    }
}
