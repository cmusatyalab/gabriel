package edu.cmu.cs.gabriel.client.socket;

import java.io.IOException;
import java.net.InetAddress;
import java.net.Socket;

import javax.net.SocketFactory;

public class SocketFactoryTcpNoDelay extends SocketFactory {
    final private SocketFactory socketFactory;

    public SocketFactoryTcpNoDelay() {
        socketFactory = SocketFactory.getDefault();
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
