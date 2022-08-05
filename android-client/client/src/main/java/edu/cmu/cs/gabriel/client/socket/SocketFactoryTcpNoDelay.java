package edu.cmu.cs.gabriel.client.socket;

import android.net.Network;
import android.util.Log;

import java.io.IOException;
import java.net.InetAddress;
import java.net.Socket;

import javax.net.SocketFactory;

public class SocketFactoryTcpNoDelay extends SocketFactory {
    private final SocketFactory socketFactory;

    public SocketFactoryTcpNoDelay() {
        Log.i("SocketFactoryTcpNoDelay", "Using default factory...");
        socketFactory = SocketFactory.getDefault();
    }
    public SocketFactoryTcpNoDelay(Network network) {
        Log.i("SocketFactoryTcpNoDelay", "Setting socket factory to that of the passed network...");
        socketFactory = network.getSocketFactory();
    }

    @Override
    public Socket createSocket() throws IOException {
        Socket socket = socketFactory.createSocket();
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(String host, int port) throws IOException {
        Socket socket = socketFactory.createSocket(host, port);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(String host, int port, InetAddress localHost, int localPort) throws
            IOException {
        Socket socket = socketFactory.createSocket(host, port, localHost, localPort);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(InetAddress host, int port) throws IOException {
        Socket socket = socketFactory.createSocket(host, port);
        socket.setTcpNoDelay(true);
        return socket;
    }

    @Override
    public Socket createSocket(InetAddress address, int port, InetAddress localAddress,
                               int localPort) throws IOException {
        Socket socket = socketFactory.createSocket(address, port, localAddress, localPort);
        socket.setTcpNoDelay(true);
        return socket;
    }
}
