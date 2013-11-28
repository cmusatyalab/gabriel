package edu.cmu.cs.gabriel.token;

import java.io.File;
import java.io.FileWriter;
import java.io.IOException;
import java.util.TreeMap;
import java.util.concurrent.ConcurrentHashMap;

import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.network.NetworkProtocol;

public class TokenController {
	private static final String LOG_TAG = "krha";
	
	private int currentToken = Const.MAX_TOKEN_SIZE;
	private ConcurrentHashMap<Long, SentPacketInfo> latencyStamps = new ConcurrentHashMap<Long, SentPacketInfo>();
    private Object tokenLock = new Object();
	private FileWriter mFileWriter = null;

	private long prevRecvedAck = 0;
	private long firstSentTime = 0;
    
    public TokenController(File resultSavingPath){
    	try{
    		mFileWriter = new FileWriter(resultSavingPath);
    		mFileWriter.write("FrameID\tEndTime(ms)\tStartTime(ms)\tLatency(ms)\n");
    	} catch (IOException ex) {
    		Log.e("Battery", "Output File", ex);
    		return;
    	}			
    }

	public Handler tokenHandler = new Handler() {
		private long frame_latency = 0;
		private long frame_size = 0;
		private long frame_latency_count = 0;
		
		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {					
				long now = System.currentTimeMillis();
				Bundle bundle = msg.getData();
				long recvFrameID = bundle.getLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID);
				if (recvFrameID > prevRecvedAck){
					long increaseCount = recvFrameID - prevRecvedAck;					
					increaseTokens(increaseCount);
					for (long index = prevRecvedAck+1; index < recvFrameID; index++){
						Log.d(LOG_TAG, "dump consumped but not acked :" + index);
						latencyStamps.remove(index);
					} 
					prevRecvedAck = recvFrameID;
					
					// get the packet information
					SentPacketInfo sentPacket = latencyStamps.remove(recvFrameID);
					if (sentPacket != null){
						long time_diff = now - sentPacket.generatedTime;
						String log = recvFrameID + "\t" + now + "\t" + sentPacket.generatedTime + "\t" + time_diff;
			    		try {
							mFileWriter.write(log + "\n");
						} catch (IOException e) {}

						frame_latency += time_diff;
						frame_size += sentPacket.sentSize;
						frame_latency_count++;
						if (frame_latency_count % 100 == 0){
//							Log.d(LOG_TAG, recvFrameID + ", size: " + sentPacket.sentSize + "\tBandwidth : " + 8.0*frame_size/(now-firstSentTime)/1000 + "(MB/s)");					
						}						
					}
				}
			}
		}
	};

	public void sendData(long frameID, long dataTime, int sentSize) {
		this.latencyStamps.put(frameID, new SentPacketInfo(dataTime, sentSize));
		if (firstSentTime == 0){
			firstSentTime = System.currentTimeMillis();
		}		
	}

	public int getCurrentToken() {
		synchronized(tokenLock){
			return this.currentToken;
		}
	}

	public void increaseTokens(long count) {
		synchronized(tokenLock){
			this.currentToken += count;		
		}
	}		

	public void decreaseToken() {
		synchronized(tokenLock){
			if (this.currentToken > 0){
				this.currentToken--;
			}
		}		
	}

	public void close() {
		latencyStamps.clear();
	}
}
