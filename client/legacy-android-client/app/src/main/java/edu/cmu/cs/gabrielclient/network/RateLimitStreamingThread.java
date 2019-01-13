package edu.cmu.cs.gabrielclient.network;

import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.util.Arrays;


/**
 * Streaming thread with rate limit capability.
 */
public class RateLimitStreamingThread extends RateLimitTCPNetworkThread {

    private static final String LOG_TAG = RateLimitStreamingThread.class.getSimpleName();

    // data shared between threads
    private Object dataLock = new Object();
    private volatile long dataID = 0;
    private long lastSentDataID = 0;
    private byte[] dataBuffer = null;

    public RateLimitStreamingThread(ConnectionConfig config) {
        super(config);
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


    private void networkSendData() throws IOException {
        byte[] data;
        Log.d(LOG_TAG, "Trying to grab data lock");
        synchronized (dataLock) {
            while (this.dataBuffer == null) {
                try {
                    Log.d(LOG_TAG, "wait for datalock");
                    dataLock.wait();
                } catch (InterruptedException e) {
                }
            }
            Log.d(LOG_TAG, "got data");
            data = Arrays.copyOf(this.dataBuffer, this.dataBuffer.length);
            lastSentDataID = this.dataID;
            this.dataBuffer = null;
        }
        Log.v(LOG_TAG, "sending:" + lastSentDataID);
        this.conn.networkWriter.write(getPacketByteArray(data));
        this.conn.networkWriter.flush();
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Streaming thread running");
        initTCPConnection();
        while (this.isRunning) {
            this.waitForTranssmisionSlot();
            Log.d(LOG_TAG, "Acquired a transmission slot");
            try {
                networkSendData();
                this.occupyTransmissionSlot();
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in sending packet: " + e);
                this.notifyError(e.getMessage());
                this.isRunning = false;
                return;
            }
        }
        this.isRunning = false;
    }
}
