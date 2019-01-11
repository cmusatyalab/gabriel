package edu.cmu.cs.gabrielclient.network;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Arrays;

import edu.cmu.cs.gabrielclient.sensorstream.SensorStreamIF;
import edu.cmu.cs.gabrielclient.token.TokenController;

/**
 * A generic streaming thread
 */
public class StreamingThread extends Thread {

    private static final String LOG_TAG = StreamingThread.class.getSimpleName();

    private boolean isRunning = false;

    // TCP connection
    private InetAddress remoteIP;
    private String serverAddress;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;

    // data shared between threads
    private Object dataLock = new Object();
    private volatile long dataID = 0;
    private long lastSentDataID = 0;
    private byte[] dataBuffer = null;

    // auxiliary objects to communicate with caller
    // and rate control
    private Handler callerHandler = null;
    private TokenController tc = null;

    public StreamingThread(SensorStreamIF.SensorStreamConfig config) {
        this(config.serverIP, config.serverPort, config.returnMsgHandler, config.tc);
    }

    public StreamingThread(String serverIP, int port, Handler handler, TokenController tc) {
        isRunning = false;
        this.callerHandler = handler;
        this.tc = tc;
        this.serverAddress = serverIP;
        this.remotePort = port;
    }

    private void initTCPConnection() {
        try {
            remoteIP = InetAddress.getByName(serverAddress);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing network socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
        }
    }

    /**
     * Get gabriel specific transmission protocol packet byte array
     *
     * @param data
     * @return
     * @throws IOException
     */
    private byte[] getPacketByteArray(byte[] data) throws IOException {
        ByteArrayOutputStream baos = new ByteArrayOutputStream();
        DataOutputStream dos = new DataOutputStream(baos);
        byte[] header = ("{\"" + NetworkProtocol.HEADER_MESSAGE_FRAME_ID + "\":" + lastSentDataID +
                "}").getBytes();
        dos.writeInt(header.length);
        dos.write(header);
        dos.writeInt(data.length);
        dos.write(data);
        return baos.toByteArray();
    }

    /**
     * Called whenever a new data is generated
     * Puts the new data into the @dataBuffer
     */
    public void send(byte[] data) {
        Log.v(LOG_TAG, "received new data");
        synchronized (dataLock) {
            this.dataBuffer = data;
            this.dataID++;
            dataLock.notify();
        }
    }

    /**
     * Check with rate control mechanism to see if I can transmit data
     *
     * @return
     */
    private boolean waitForTranssmisionSlot() {
        // getCurrentToken is blocking
        if (this.tc != null && this.tc.getCurrentToken() > 0) {
            return true;
        }
        return false;
    }

    private void occupyTransmissionSlot(){
        if (this.tc != null){
            // send packet and consume tokens
//            this.tc.logSentPacket(lastSentFrameID, dataTime, compressedTime);
            this.tc.decreaseToken();
        }
    }

    private void networkSendData() throws IOException{
        byte[] data;
        synchronized (dataLock) {
            while (this.dataBuffer == null) {
                try {
                    dataLock.wait();
                } catch (InterruptedException e) {
                }
            }
            data = Arrays.copyOf(this.dataBuffer, this.dataBuffer.length);
            lastSentDataID = this.dataID;
            this.dataBuffer = null;
        }
        Log.v(LOG_TAG, "sending:" + lastSentDataID);
        networkWriter.write(getPacketByteArray(data));
        networkWriter.flush();
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Streaming thread running");
        initTCPConnection();
        while (this.isRunning) {
            waitForTranssmisionSlot();
            try {
                networkSendData();
                occupyTransmissionSlot();
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in sending packet: " + e);
                this.notifyError(e.getMessage());
                this.isRunning = false;
                return;
            }
        }
        this.isRunning = false;
    }

    public void stopStreaming() {
        isRunning = false;
        if (tcpSocket != null) {
            try {
                tcpSocket.close();
            } catch (IOException e) {
            }
        }
        if (networkWriter != null) {
            try {
                networkWriter.close();
            } catch (IOException e) {
            }
        }
    }

    /**
     * Notifies error to the calling thread
     */
    private void notifyError(String message) {
        // callback
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_RET_FAILED;
        Bundle data = new Bundle();
        data.putString("message", message);
        msg.setData(data);
        this.callerHandler.sendMessage(msg);
    }
}
