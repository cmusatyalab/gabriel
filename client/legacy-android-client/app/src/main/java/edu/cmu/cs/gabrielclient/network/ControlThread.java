package edu.cmu.cs.gabrielclient.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.net.UnknownHostException;
import java.util.LinkedList;
import java.util.Queue;

import org.json.JSONException;
import org.json.JSONObject;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import edu.cmu.cs.gabrielclient.Const;
import edu.cmu.cs.gabrielclient.token.TokenController;

public class ControlThread extends Thread {

    private static final String LOG_TAG = "Control";

    private boolean isRunning = false;
    
    Queue<String> cmdQueue = new LinkedList<String>();

    // TCP connection
    private InetAddress remoteIP;
    private String serverAddress;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
    private DataInputStream networkReader = null;

    private Object cmdLock = new Object();

    private Handler mainHandler = null;
    private TokenController tokenController = null;

    public ControlThread(String serverIP, int port, Handler handler, TokenController tokenController) {
        isRunning = false;
        this.mainHandler = handler;
        this.tokenController = tokenController;
        serverAddress = serverIP;
        remotePort = port;
    }

    /**
     * @return a String representing the received message from @reader
     */
    private String receiveMsg(DataInputStream reader) throws IOException {
        int retLength = reader.readInt();
        byte[] recvByte = new byte[retLength];
        int readSize = 0;
        while(readSize < retLength){
            int ret = reader.read(recvByte, readSize, retLength-readSize);
            if(ret <= 0){
                break;
            }
            readSize += ret;
        }
        String receivedString = new String(recvByte);
        return receivedString;
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Streaming thread running");
        try {
            remoteIP = InetAddress.getByName(serverAddress);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        // initialization of the TCP connection
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.setSoTimeout(0);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 0);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
            networkReader = new DataInputStream(tcpSocket.getInputStream());
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing network socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
            return;
        }

        while (this.isRunning) {
            // first check if server has sent any message
            try {
                String controlMsg = this.receiveMsg(networkReader);
                Log.i(LOG_TAG, "Got server control message: " + controlMsg);
                
                if (controlMsg != null){
                    Message msg = Message.obtain();
                    msg.what = NetworkProtocol.NETWORK_RET_CONFIG;
                    msg.obj = controlMsg;
                    this.mainHandler.sendMessage(msg);
                }
            } catch (SocketTimeoutException e) {
                Log.d(LOG_TAG, "no server command");
            } catch (IOException e) {
                Log.e(LOG_TAG, "network error: " + e);
            }

            // next check if the client has any message to send
            String command = null;
            synchronized(cmdLock){
                if (!this.cmdQueue.isEmpty()){
                    command = cmdQueue.remove();
                }
            }
            if (command == null) continue;
            Log.i(LOG_TAG, "Processing command from client:" + command);

            if (command.equals("ping")) {
                try {
                    // process commands
                    long min_diff = 1000000;
                    long bestSentTime = 0, bestServerTime = 0, bestRecvTime = 0;
                    for (int i = 0; i < Const.MAX_PING_TIMES; i++) {
                        // send current time to server
                        ByteArrayOutputStream baos = new ByteArrayOutputStream();
                        DataOutputStream dos = new DataOutputStream(baos);
                        long sentTime = System.currentTimeMillis();
                        byte[] jsonData = ("{\"sync_time\":" + sentTime + "}").getBytes();
                        dos.writeInt(jsonData.length);
                        dos.write(jsonData);
                        networkWriter.write(baos.toByteArray());
                        networkWriter.flush();

                        // receive current time at server
                        String recvMsg = this.receiveMsg(networkReader);
                        long serverTime = -1;
                        try {
                            JSONObject obj = new JSONObject(recvMsg);
                            serverTime = obj.getLong("sync_time");
                        } catch (JSONException e) {
                            Log.e(LOG_TAG, "Sync time with server error!!");
                        }

                        long recvTime = System.currentTimeMillis();
                        if (recvTime - sentTime < min_diff) {
                            min_diff = recvTime - sentTime;
                            bestSentTime = sentTime;
                            bestServerTime = serverTime;
                            bestRecvTime = recvTime;
                        }
                    }

                    // send message to token controller, actually for logging...
                    Message msg = Message.obtain();
                    msg.what = NetworkProtocol.NETWORK_RET_SYNC;
                    String sync_str = "" + bestSentTime + "\t" + bestServerTime + "\t" + bestRecvTime + "\n";
                    msg.obj = sync_str;
                    Log.i(LOG_TAG, sync_str);
                    tokenController.writeString(sync_str);
                    //tokenController.tokenHandler.sendMessage(msg);

                } catch (SocketException e) {
                    Log.v(LOG_TAG, "no server command");
                } catch (IOException e) {
                    Log.e(LOG_TAG, "Error in sending packet: " + e);
                    this.notifyError(e.getMessage());
                    this.isRunning = false;
                    return;
                }
            }
        }
        this.isRunning = false;
    }

    /**
     * Called to send a new control message to the server
     */
    public void sendControlMsg(String command) {
        Log.v(LOG_TAG, "++sendControlMsg");
        synchronized (cmdLock) {
            cmdQueue.add(command);
        }
    }

    public void close() {
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
        this.mainHandler.sendMessage(msg);
    }

}
