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

	private int currentToken = Const.MAX_TOKEN_SIZE;
	private ConcurrentHashMap<Long, SentPacketInfo> latencyStamps = new ConcurrentHashMap<Long, SentPacketInfo>();
	private Object tokenLock = new Object();
	private FileWriter mFileWriter = null;

	private long prevRecvedAck = 0;
	private long firstSentTime = 0;

	public TokenController(File resultSavingPath) {
		try {
			mFileWriter = new FileWriter(resultSavingPath);
			mFileWriter.write("FrameID\tEngineID\tStartTime\tCompressedTime\tRecvTime\tDoneTime\tStatus\n");
		} catch (IOException ex) {
			Log.e(LOG_TAG, "Output File Error", ex);
			return;
		}
	}

	public Handler tokenHandler = new Handler() {

		public void handleMessage(Message msg) {
			Log.d(LOG_TAG, "+handle message");
			if (msg.what == NetworkProtocol.NETWORK_RET_SYNC) {
				try {
					if (Const.IS_EXPERIMENT){
						String log = (String) msg.obj;
						mFileWriter.write(log);
					}
				} catch (IOException e) {}
			}
			if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {
				Log.d(LOG_TAG, "+network_ret");
				ReceivedPacketInfo receivedPacket = (ReceivedPacketInfo) msg.obj;
				long recvFrameID = receivedPacket.frameID;
				String recvEngineID = receivedPacket.engineID;
				long increaseCount = 0;
				for (long index = prevRecvedAck + 1; index < recvFrameID; index++) {
					SentPacketInfo sentPacket = null; 
					if (Const.IS_EXPERIMENT){
						// Do not remove since we need to measure latency even for the late response
						sentPacket = latencyStamps.get(index);
					}else{
						sentPacket = latencyStamps.remove(index);						
					}
					if (sentPacket != null) {
						increaseCount++;
//						Log.d(LOG_TAG, "dump consumped but not acked :" + index);
					}
				}
				increaseTokens(increaseCount);

				// get the packet information
				SentPacketInfo sentPacket = latencyStamps.get(recvFrameID);
				if (sentPacket != null) {
					if (recvFrameID > prevRecvedAck) {
						// do not increase token if you already
						// received duplicated ack from other cognitive engine
						increaseTokens(1);
					}

					try {
						if (Const.IS_EXPERIMENT){
							String log = recvFrameID + "\t" + recvEngineID + "\t"
									+ sentPacket.generatedTime + "\t" + sentPacket.compressedTime + "\t" + receivedPacket.msg_recv_time + "\t" 
									+ receivedPacket.guidance_done_time + "\t" + receivedPacket.status;
							mFileWriter.write(log + "\n");
						}
					} catch (IOException e) {}
				}
				prevRecvedAck = recvFrameID;
			}
		}
	};

	public void sendData(long frameID, long dataTime, long compressedTime, int sentSize) {
		this.latencyStamps.put(frameID, new SentPacketInfo(dataTime, compressedTime, sentSize));
		if (firstSentTime == 0) {
			firstSentTime = System.currentTimeMillis();
		}
	}

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
		latencyStamps.clear();
		try {
			mFileWriter.close();
		} catch (IOException e) {
			// TODO Auto-generated catch block
			e.printStackTrace();
		}
	}
}
