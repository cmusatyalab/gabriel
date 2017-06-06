package edu.cmu.cs.gabriel.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Vector;

import edu.cmu.cs.gabriel.token.TokenController;

import android.hardware.Camera.Size;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

public class AccStreamingThread extends Thread {
    private static final String LOG_TAG = "AccStreaming";

    private boolean isRunning = false;

    // TCP connection
    private InetAddress remoteIP;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
//    private DataInputStream networkReader = null;

    // The ACC data
    private Vector<AccData> accDataList = new Vector<AccData>();

    private Handler networkHander = null;
    private TokenController tokenController = null;

    private long frameID;

    class AccData {
        public float x, y, z;
        public AccData(float x, float y, float z) {
            this.x = x;
            this.y = y;
            this.z = z;
        }
    }

    public AccStreamingThread(String serverIP, int port, Handler handler, TokenController tokenController) {
        isRunning = false;
        this.networkHander = handler;
        this.tokenController = tokenController;
        this.frameID = 0;

        try {
            remoteIP = InetAddress.getByName(serverIP);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        remotePort = port;
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "ACC streaming thread running");

        // initialization of the TCP connection
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
//            networkReader = new DataInputStream(tcpSocket.getInputStream());
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
            return;
        }

        while (this.isRunning) {
            try {
                if (this.accDataList.size() == 0){
                    try {
                        Thread.sleep(10); // 10 millisecond
                    } catch (InterruptedException e) {}
                    continue;
                }

                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                DataOutputStream dos = new DataOutputStream(baos);
                while (this.accDataList.size() > 0) {
                    AccData data = this.accDataList.remove(0);
                    dos.writeFloat(data.x);
                    dos.writeFloat(data.y);
                    dos.writeFloat(data.z);
                }

                byte[] header = ("{\"" + NetworkProtocol.HEADER_MESSAGE_FRAME_ID + "\":" + this.frameID + "}").getBytes();
                byte[] data = baos.toByteArray();
                networkWriter.writeInt(header.length);
                networkWriter.write(header);
                networkWriter.writeInt(data.length);
                networkWriter.write(data);
                networkWriter.flush();
                this.frameID++;

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
            } catch (IOException e) {}
        }
        if (networkWriter != null) {
            try {
                networkWriter.close();
            } catch (IOException e) {}
        }
    }

    public void push(float[] sensor) {
        this.accDataList.add(new AccData(sensor[0], sensor[1], sensor[2]));
    }

    /**
     * Notifies error to the main thread
     */
    private void notifyError(String message) {
        // callback
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_RET_FAILED;
        Bundle data = new Bundle();
        data.putString("message", message);
        msg.setData(data);
        this.networkHander.sendMessage(msg);
    }
}
