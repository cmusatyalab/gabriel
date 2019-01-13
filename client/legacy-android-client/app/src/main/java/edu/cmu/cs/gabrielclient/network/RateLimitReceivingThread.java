package edu.cmu.cs.gabrielclient.network;

import android.os.Message;
import android.util.Log;

import java.io.DataInputStream;
import java.io.IOException;

/**
 * Server result receiving thread.
 * It will refill the tokens of the rate limiter.
 */
public class RateLimitReceivingThread extends RateLimitTCPNetworkThread {

    private static final String LOG_TAG = RateLimitReceivingThread.class.getSimpleName();

    public RateLimitReceivingThread(ConnectionConfig config) {
        super(config);
    }

    @Override
    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "start running");
        initTCPConnection();

        while (isRunning == true) {
            try {
                String recvMsg = this.receiveGabrielMsg(this.conn.networkReader);
                //TODO(junjuew): increase token here. Is this implementation correct?
                this.tc.increaseTokens(1);
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_MESSAGE;
                msg.obj = recvMsg;
                this.callerHandler.sendMessage(msg);
            } catch (IOException e) {
                Log.w(LOG_TAG, "Error in receiving result, maybe because the app has paused");
                this.notifyError(e.getMessage());
                break;
            }
        }
    }

    /**
     * Receive Gabriel-formatted (header length, header, data length, data) message
     *
     * @return a String representing the received message from @reader
     */
    private String receiveGabrielMsg(DataInputStream reader) throws IOException {
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
}
