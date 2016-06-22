package edu.cmu.cs.gabriel.token;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.concurrent.ConcurrentHashMap;

import android.os.Handler;
import android.os.Message;
import android.util.Log;
import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.network.NetworkProtocol;

public class TokenController {
    private static final String LOG_TAG = "TokenController";

    // the number of tokens remained
    private int currentToken = 0;
    
    // information about all sent packets, the key is the frameID and the value documents relevant timestamps
    private ConcurrentHashMap<Long, SentPacketInfo> sentPackets = new ConcurrentHashMap<Long, SentPacketInfo>();
    
    private Object tokenLock = new Object();
    
    private FileWriter fileWriter = null;

    // timestamp when the last ACK was received
    private long prevRecvFrameID = 0;

    public TokenController(int tokenSize, File resultSavingPath) {
        this.currentToken = tokenSize;
        if (Const.IS_EXPERIMENT) {
            try {
                fileWriter = new FileWriter(resultSavingPath);
                fileWriter.write("FrameID\tEngineID\tStartTime\tCompressedTime\tRecvTime\tDoneTime\tStatus\n");
            } catch (IOException e) {
                Log.e(LOG_TAG, "Result file cannot be properly opened", e);
            }
        }
    }

    public Handler tokenHandler = new Handler() {

        public void handleMessage(Message msg) {
            if (msg.what == NetworkProtocol.NETWORK_RET_SYNC) {
                try {
                    if (Const.IS_EXPERIMENT){
                        String log = (String) msg.obj;
                        fileWriter.write(log);
                    }
                } catch (IOException e) {}
            }
            if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {
                ReceivedPacketInfo receivedPacket = (ReceivedPacketInfo) msg.obj;
                long recvFrameID = receivedPacket.frameID;
                String recvEngineID = receivedPacket.engineID;
                
                // increase appropriate amount of tokens
                long increaseCount = 0;
                for (long frameID = prevRecvFrameID + 1; frameID < recvFrameID; frameID++) {
                    SentPacketInfo sentPacket = null;
                    if (Const.IS_EXPERIMENT) {
                        // Do not remove since we need to measure latency even for the late response
                        sentPacket = sentPackets.get(frameID);
                    } else {
                        sentPacket = sentPackets.remove(frameID);
                    }
                    if (sentPacket != null) {
                        increaseCount++;
                    }
                }
                increaseTokens(increaseCount);

                // deal with the current response
                SentPacketInfo sentPacket = sentPackets.get(recvFrameID);
                if (sentPacket != null) {
                    // do not increase token if have already received duplicated ack
                    if (recvFrameID > prevRecvFrameID) {    
                        increaseTokens(1);
                    }

                    if (Const.IS_EXPERIMENT) {
                        try {
                            String log = recvFrameID + "\t" + recvEngineID + "\t" +
                                    sentPacket.generatedTime + "\t" + sentPacket.compressedTime + "\t" + 
                                    receivedPacket.msgRecvTime + "\t" + receivedPacket.guidanceDoneTime + "\t" + 
                                    receivedPacket.status;
                            fileWriter.write(log + "\n");
                        } catch (IOException e) {}
                    }
                }
                prevRecvFrameID = recvFrameID;
            }
        }
    };

    public void logSentPacket(long frameID, long dataTime, long compressedTime) {
        this.sentPackets.put(frameID, new SentPacketInfo(dataTime, compressedTime));
    }

    /**
     * Blocks and only returns when token > 0
     * @return the current token number
     */
    public int getCurrentToken() {
        synchronized (tokenLock) {
            if (this.currentToken > 0) {
                return this.currentToken;
            } else {
                try {
                    tokenLock.wait();
                } catch (InterruptedException e) {}
                return this.currentToken;
            }
        }
    }

    public void increaseTokens(long count) {
        synchronized (tokenLock) {
            this.currentToken += count;
            this.tokenLock.notify();
        }
    }

    public void decreaseToken() {
        synchronized (tokenLock) {
            if (this.currentToken > 0) {
                this.currentToken--;
            }
            this.tokenLock.notify();
        }
    }

    public void close() {
        sentPackets.clear();
        if (Const.IS_EXPERIMENT) {
            try {
                fileWriter.close();
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in closing latency file");
            }
        }
    }
}
