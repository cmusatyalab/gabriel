package edu.cmu.cs.gabrielclient.network;

import android.os.Handler;
import android.os.Message;
import android.util.Log;

import java.io.IOException;
import java.net.InetAddress;

import edu.cmu.cs.gabrielclient.control.TokenController;

import static edu.cmu.cs.gabrielclient.network.TCPConnection.getIPFromString;

public class RateLimitTCPNetworkThread extends Thread {
    private static final String LOG_TAG = RateLimitTCPNetworkThread.class.getSimpleName();
    boolean isRunning;
    // TCP connection
    InetAddress remoteIP;
    int remotePort;
    TCPConnection conn;
    Handler callerHandler;
    // rate limited
    TokenController tc;

    public RateLimitTCPNetworkThread(ConnectionConfig config) {
        this(config.serverIP, config.serverPort, config.callerHandler, config.tc);
    }

    public RateLimitTCPNetworkThread(String serverIP, int port, Handler callerHandler, TokenController tc) {
        this.isRunning = false;
        this.callerHandler = callerHandler;
        this.remoteIP = getIPFromString(serverIP);
        this.remotePort = port;
        this.tc = tc;
    }

    void initTCPConnection() {
        try {
            conn = new TCPConnection(this.remoteIP, this.remotePort);
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
        }
    }

    void notifyError(String errorMessage) {
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_CONNECT_FAILED;
        msg.obj = errorMessage;
        this.callerHandler.sendMessage(msg);
    }

    public void close() {
        this.isRunning = false;
        this.conn.close();
    }

    /**
     * Check with rate control mechanism to see if I can transmit data
     *
     * @return
     */
    boolean waitForTranssmisionSlot() {
        // getCurrentToken is blocking
        if (this.tc != null && this.tc.getCurrentToken() > 0) {
            return true;
        }
        return false;
    }

    void occupyTransmissionSlot() {
        if (this.tc != null) {
            // send packet and consume tokens
//            this.tc.logSentPacket(lastSentFrameID, dataTime, compressedTime);
            this.tc.decreaseToken();
        }
    }
}
