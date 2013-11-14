package edu.cmu.cs.gabriel.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.TreeMap;
import java.util.Vector;

import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera.Parameters;
import android.hardware.Camera.Size;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

public class AccStreamingThread extends Thread {
	private static final String LOG_TAG = "krha";

	private static final int MAX_TOKEN_SIZE = 5;
	private int currentToken = MAX_TOKEN_SIZE;

	static final int BUFFER_SIZE = 102400; // only for the UDP case
	private boolean is_running = false;
	private InetAddress remoteIP;
	private int remotePort;

	private Socket tcpSocket = null;
	private DataOutputStream networkWriter = null;
	private AccControlThread networkReceiver = null;

	private Vector<AccData> accDataList = new Vector<AccData>();
	private Handler networkHander = null;
	private long frameID = 0;
	private TreeMap<Long, SentPacketInfo> latencyStamps = new TreeMap<Long, SentPacketInfo>();
	
	class AccData{
		public int sentTime;
		public float[] acc;
		public AccData(int time, float[] s) {
			sentTime = time;
			acc = s;
		}
	}
	
	class SentPacketInfo{
		public long sentTime;
		public int sentSize;
		
		public SentPacketInfo(long currentTimeMillis, int sentSize) {
			this.sentTime = currentTimeMillis;
			this.sentSize = sentSize;
		}
	}

	public AccStreamingThread(String IPString, int port, Handler handler) {
		is_running = false;
		this.networkHander = handler;
		try {
			remoteIP = InetAddress.getByName(IPString);
		} catch (UnknownHostException e) {
			Log.e(LOG_TAG, "unknown host: " + e.getMessage());
		}
		remotePort = port;
	}

	public void run() {
		this.is_running = true;
		Log.i(LOG_TAG, "ACC thread running");

		try {
			tcpSocket = new Socket();
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			DataInputStream networkReader = new DataInputStream(tcpSocket.getInputStream());
			networkReceiver = new AccControlThread(networkReader, this.networkHander, this.tokenHandler);
			networkReceiver.start();
		} catch (IOException e) {
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e.getMessage());
			this.notifyError(e.getMessage());
			this.is_running = false;
			return;
		}

		while (this.is_running) {
			try {
				if (this.accDataList.size() == 0){
					try {
						Thread.sleep(10);
					} catch (InterruptedException e) {}
					continue;
				}
				
		        ByteArrayOutputStream baos =new ByteArrayOutputStream();
		        DataOutputStream dos=new DataOutputStream(baos);				
				while (this.accDataList.size() > 0) {
					AccData data = this.accDataList.remove(0);
					dos.writeInt(data.sentTime);
					dos.writeFloat(data.acc[0]);
					dos.writeFloat(data.acc[1]);
					dos.writeFloat(data.acc[2]);
				}

				byte[] header = ("{\"id\":" + this.frameID + "}").getBytes();
		        byte[] data = baos.toByteArray();
				networkWriter.writeInt(header.length);
				networkWriter.writeInt(data.length);
				networkWriter.write(header);
				networkWriter.write(data);
				networkWriter.flush();
				latencyStamps.put(this.frameID, new SentPacketInfo(System.currentTimeMillis(), data.length
						+ header.length));
				this.frameID++;
				if (this.currentToken > 0) {
					this.currentToken--;
				}
			} catch (IOException e) {
				Log.e(LOG_TAG, e.getMessage());
				this.notifyError(e.getMessage());
				this.is_running = false;
				return;
			}
		}
		this.is_running = false;
	}

	public boolean stopStreaming() {
		is_running = false;
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
		if (networkReceiver != null) {
			networkReceiver.close();
		}

		return true;
	}

	private long sentframeCount = 0;
	private long firstStartTime = 0, lastSentTime = 0;
	private long prevUpdateTime = 0, currentUpdateTime = 0;
	private Size cameraImageSize = null;


	public void push(float[] sensor) {
		if (firstStartTime == 0) {
			firstStartTime = System.currentTimeMillis();
		}
		currentUpdateTime = System.currentTimeMillis();
		sentframeCount++;
		this.accDataList.add(new AccData((int)(currentUpdateTime-firstStartTime), sensor));
		prevUpdateTime = currentUpdateTime;
	}
	
	private void notifyError(String message) {
		// callback
		Message msg = Message.obtain();
		msg.what = NetworkProtocol.NETWORK_RET_FAILED;
		Bundle data = new Bundle();
		data.putString("message", message);
		msg.setData(data);		
		this.networkHander.sendMessage(msg);
	}

	private Handler tokenHandler = new Handler() {

		private long frame_latency = 0;
		private long frame_size = 0;
		private long frame_latency_count = 0;
		
		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {
				Bundle bundle = msg.getData();
				long recvFrameID = bundle.getLong(VideoControlThread.MESSAGE_FRAME_ID);
				SentPacketInfo sentPacket = latencyStamps.remove(recvFrameID);
				if (sentPacket != null){
					long time_diff = System.currentTimeMillis() - sentPacket.sentTime;
					frame_latency += time_diff;
					frame_size += sentPacket.sentSize;
					frame_latency_count++;
					if (frame_latency_count % 10 == 0){
					}				
				}
				if (latencyStamps.size() > 30*60){
					latencyStamps.clear();
				}
				
				increaseToken();
			}
		}
	};
		
	public int getCurrentToken(){
		return this.currentToken;
	}
	
	public void increaseToken(){
		this.currentToken++;
	}
}
