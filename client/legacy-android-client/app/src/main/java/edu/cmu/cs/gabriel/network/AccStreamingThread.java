package edu.cmu.cs.gabriel.network;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.ArrayList;
import java.util.Vector;

import edu.cmu.cs.gabriel.Const;
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

    // ACC data for experiments
    private ArrayList<AccData> accRecordedData = null;
    private int accRecordedDataIdx = -1;

    private Handler networkHander = null;
    private TokenController tokenController = null;
    private LogicalTime logicalTime = null;

    private long frameID;

    class AccData {
        public float x, y, z;
        public long timestamp;
        public AccData(float x, float y, float z) {
            this.x = x;
            this.y = y;
            this.z = z;
            this.timestamp = -1;
        }
        public AccData(float x, float y, float z, long timestamp) {
            this.x = x;
            this.y = y;
            this.z = z;
            this.timestamp = timestamp;
        }
    }

    public AccStreamingThread(String serverIP, int port, Handler handler, TokenController tokenController, LogicalTime logicalTime) {
        isRunning = false;
        this.networkHander = handler;
        this.tokenController = tokenController;
        this.logicalTime = logicalTime;
        this.frameID = 0;

        try {
            remoteIP = InetAddress.getByName(serverIP);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        remotePort = port;

        if (Const.LOAD_ACC) {
            try {
                accRecordedData = new ArrayList<AccData>();

                BufferedReader br = new BufferedReader(new FileReader(Const.TEST_ACC_FILE));
                String line = null;
                while ((line = br.readLine()) != null) {
                    String tokens[] = line.split(",");
                    long timestamp = Long.parseLong(tokens[0]);
                    float x = Float.parseFloat(tokens[1]);
                    float y = Float.parseFloat(tokens[2]);
                    float z = Float.parseFloat(tokens[3]);
                    accRecordedData.add(new AccData(x, y, z, timestamp));
                }

                accRecordedDataIdx = 0;
            } catch (IOException e) {
            }
        }
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
        if (!Const.LOAD_ACC) {
            this.accDataList.add(new AccData(sensor[0], sensor[1], sensor[2]));
        } else {
            AccData dataCurrent = accRecordedData.get(accRecordedDataIdx);
            accRecordedDataIdx = (accRecordedDataIdx + 1) % accRecordedData.size();
            this.accDataList.add(new AccData(dataCurrent.x, dataCurrent.y, dataCurrent.z));

            this.logicalTime.updateAccTime(dataCurrent.timestamp);
        }
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
