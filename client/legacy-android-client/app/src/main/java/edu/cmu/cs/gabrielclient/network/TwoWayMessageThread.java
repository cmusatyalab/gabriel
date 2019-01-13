package edu.cmu.cs.gabrielclient.network;

import android.os.Message;
import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.SocketException;
import java.net.SocketTimeoutException;
import java.util.LinkedList;
import java.util.Queue;

import edu.cmu.cs.gabrielclient.Const;

public class TwoWayMessageThread extends RateLimitTCPNetworkThread {
    private static final String LOG_TAG = TwoWayMessageThread.class.getSimpleName();
    Queue<String> cmdQueue = new LinkedList<String>();
    private Object cmdLock = new Object();

    public TwoWayMessageThread(ConnectionConfig config) {
        super(config);
    }

    /**
     * @return a String representing the received message from @reader
     */
    private String receiveMsg(DataInputStream reader) throws IOException {
        int retLength = reader.readInt();
        byte[] recvByte = new byte[retLength];
        int readSize = 0;
        while (readSize < retLength) {
            int ret = reader.read(recvByte, readSize, retLength - readSize);
            if (ret <= 0) {
                break;
            }
            readSize += ret;
        }
        String receivedString = new String(recvByte);
        return receivedString;
    }

    public void sendControlMsg(String command) {
        Log.v(LOG_TAG, "++sendControlMsg");
        synchronized (cmdLock) {
            cmdQueue.add(command);
        }
    }

    public void checkServerMsg() {
        try {
            String controlMsg = this.receiveMsg(this.conn.networkReader);
            Log.i(LOG_TAG, "Got server control message: " + controlMsg);

            if (controlMsg != null) {
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_CONFIG;
                msg.obj = controlMsg;
                this.callerHandler.sendMessage(msg);
            }
        } catch (SocketTimeoutException e) {
            Log.d(LOG_TAG, "no server command");
        } catch (IOException e) {
            Log.e(LOG_TAG, "network error: " + e);
        }
    }

    public String getClientMsg() {
        String command = null;
        synchronized (cmdLock) {
            if (!this.cmdQueue.isEmpty()) {
                command = cmdQueue.remove();
            }
        }
        return command;
    }

    /**
     * Send a ping command and sync time with server
     */
    public void sendPingAndSyncTime() {
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
                this.conn.networkWriter.write(baos.toByteArray());
                this.conn.networkWriter.flush();

                // receive current time at server
                String recvMsg = this.receiveMsg(this.conn.networkReader);
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
            String sync_str = "" + bestSentTime + "\t" + bestServerTime + "\t" +
                    bestRecvTime + "\n";
            msg.obj = sync_str;
            Log.i(LOG_TAG, sync_str);
            this.tc.writeString(sync_str);
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

    public void sendClientMsg(String command) {
        if (command.equals("ping")) {
            sendPingAndSyncTime();
        }
    }

    public void run() {
        this.isRunning = true;
        initTCPConnection();
        while (this.isRunning) {
            checkServerMsg();
            String command = getClientMsg();
            if (command == null) continue;
            Log.i(LOG_TAG, "Processing command from client:" + command);
            sendClientMsg(command);
        }
        this.isRunning = false;
    }
}
