package edu.cmu.cs.gabriel.token;

import java.util.TreeMap;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import edu.cmu.cs.gabriel.network.NetworkProtocol;

public class TokenController {
	private static final int MAX_TOKEN_SIZE = 10;
	private int currentToken = MAX_TOKEN_SIZE;
	private TreeMap<Long, SentPacketInfo> latencyStamps = new TreeMap<Long, SentPacketInfo>();
    private Object tokenLock = new Object();


	public Handler tokenHandler = new Handler() {
		private long frame_latency = 0;
		private long frame_size = 0;
		private long frame_latency_count = 0;
		
		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {
				Bundle bundle = msg.getData();
				long recvFrameID = bundle.getLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID);
				SentPacketInfo sentPacket = latencyStamps.remove(recvFrameID);
				if (sentPacket != null){
					long time_diff = System.currentTimeMillis() - sentPacket.sentTime;
					frame_latency += time_diff;
					frame_size += sentPacket.sentSize;
					frame_latency_count++;
					if (frame_latency_count % 10 == 0){
					}				
				}
				
				increaseToken();
			}
		}
	};

	public void sendData(long frameID, int sentSize) {
		this.latencyStamps.put(frameID, new SentPacketInfo(System.currentTimeMillis(), sentSize));		
	}

	public int getCurrentToken() {
		synchronized(tokenLock){
			return this.currentToken;
		}
	}

	public void increaseToken() {
		synchronized(tokenLock){
			this.currentToken++;
		}
	}

	public void increaseTokens(int count) {
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
