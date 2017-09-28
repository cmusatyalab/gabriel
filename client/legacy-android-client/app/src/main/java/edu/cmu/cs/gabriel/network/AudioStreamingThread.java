package edu.cmu.cs.gabriel.network;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Arrays;
import java.util.Vector;

import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.token.TokenController;

public class AudioStreamingThread extends Thread {
    private static final String LOG_TAG = "AudioStreaming";

    private boolean isRunning = false;

    // TCP connection
    private InetAddress remoteIP;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
//    private DataInputStream networkReader = null;

    // The audio data
    private ByteArrayOutputStream audioStream = null;
    private Object audioLock = new Object();
    private byte[] audioData; // preloaded audio data
    private int audioDataIdx = 0;

    private Handler networkHander = null;
    private TokenController tokenController = null;

    private long frameID;

    public AudioStreamingThread(String serverIP, int port, Handler handler, TokenController tokenController) {
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

        audioStream = new ByteArrayOutputStream();

        if (Const.LOAD_AUDIO) {
            try {
                int dataSize = (int) Const.TEST_AUDIO_FILE.length();
                Log.d(LOG_TAG, "audio file size: " + dataSize);
                FileInputStream fi = new FileInputStream(Const.TEST_AUDIO_FILE);
                audioData = new byte[dataSize];
                int ret = fi.read(audioData, 0, dataSize);
                Log.d(LOG_TAG, "# of bytes read: " + ret);
            } catch (FileNotFoundException e) {
            } catch (IOException e) {
            }
        }
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Audio streaming thread running");

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
                if (this.audioStream.size() == 0){
                    try {
                        Thread.sleep(10); // 10 millisecond
                    } catch (InterruptedException e) {}
                    continue;
                }
                byte[] data;
                synchronized (audioLock) {
                    data = audioStream.toByteArray();
                    audioStream.reset();
                }

                byte[] header = ("{\"" + NetworkProtocol.HEADER_MESSAGE_FRAME_ID + "\":" + this.frameID + "}").getBytes();
                networkWriter.writeInt(header.length);
                networkWriter.write(header);
                networkWriter.writeInt(data.length);
                networkWriter.write(data);
                networkWriter.flush();

                this.frameID++;

                try {
                    Thread.sleep(30);
                } catch (InterruptedException e) {}

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

    public void push(byte[] data) {
        synchronized (audioLock) {
            try {
                if (!Const.LOAD_AUDIO) {
                    audioStream.write(data);
                } else {
                    int l = data.length;
                    if (audioDataIdx + l <= audioData.length) {
                        byte[] dataCurrent = Arrays.copyOfRange(audioData, audioDataIdx, audioDataIdx + l);
                        audioStream.write(dataCurrent);
                    } else {
                        byte[] dataCurrent = Arrays.copyOfRange(audioData, audioDataIdx, audioData.length);
                        audioStream.write(dataCurrent);
                    }
                    audioDataIdx = (audioDataIdx + l) % audioData.length;
                }
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in writing data to audio stream: " + e.getMessage());
            }
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
