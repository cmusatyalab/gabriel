package edu.cmu.cs.gabrielclient.util;

import android.util.Log;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;

public class Common {
    private static final String LOG_TAG = Common.class.getSimpleName();

    public static InetAddress getIPFromString(String ipString) {
        InetAddress ret = null;
        try {
            ret = InetAddress.getByName(ipString);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        return ret;
    }

    public static class TCPConnection {
        public Socket tcpSocket;
        public DataOutputStream networkWriter;
        public DataInputStream networkReader;
        public InetAddress ip;
        public int port;

        public TCPConnection(InetAddress remoteIP, int remotePort) throws IOException{
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
            networkReader = new DataInputStream(tcpSocket.getInputStream());
        }

        public void close() {
            try {
                if (this.networkReader != null) {
                    this.networkReader.close();
                    this.networkReader = null;
                }
            } catch (IOException e) {
            }
            try {
                if (this.networkWriter != null) {
                    this.networkWriter.close();
                    this.networkWriter = null;
                }
            } catch (IOException e) {
            }
            try {
                if (this.tcpSocket != null) {
                    this.tcpSocket.shutdownInput();
                    this.tcpSocket.shutdownOutput();
                    this.tcpSocket.close();
                    this.tcpSocket = null;
                }
            } catch (IOException e) {
            }
        }
    }
}
