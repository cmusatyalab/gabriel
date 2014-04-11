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

import edu.cmu.cs.gabriel.token.TokenController;

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

	static final int BUFFER_SIZE = 102400; // only for the UDP case
	private boolean is_running = false;
	private InetAddress remoteIP;
	private int remotePort;

	private Socket tcpSocket = null;
	private DataOutputStream networkWriter = null;
	private AccControlThread networkReceiver = null;

	private Vector<AccData> accDataList = new Vector<AccData>();
	private TokenController tokenController = null;
	private Handler networkHander = null;
	private long frameID = 0;
	
	class AccData{
		public int sentTime;
		public float x, y, z;
		public AccData(int time, float x, float y, float z) {
			sentTime = time;
			this.x = x;
			this.y = y;
			this.z = z;
		}
	}

	public AccStreamingThread(String IPString, int port, Handler handler, TokenController tokenController) {
		is_running = false;
		this.networkHander = handler;
		this.tokenController = tokenController;
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
			tcpSocket.setTcpNoDelay(true);
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			DataInputStream networkReader = new DataInputStream(tcpSocket.getInputStream());
			networkReceiver = new AccControlThread(networkReader, this.networkHander);
			networkReceiver.start();
		} catch (IOException e) {
		    Log.e(LOG_TAG, Log.getStackTraceString(e));
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
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
				
		        ByteArrayOutputStream baos = new ByteArrayOutputStream();
		        DataOutputStream dos=new DataOutputStream(baos);				
				while (this.accDataList.size() > 0) {
					AccData data = this.accDataList.remove(0);
					dos.writeInt(data.sentTime);
					dos.writeFloat(data.x);
					dos.writeFloat(data.y);
					dos.writeFloat(data.z);
				}

				byte[] header = ("{\"id\":" + this.frameID + "}").getBytes();
		        byte[] data = baos.toByteArray();
				networkWriter.writeInt(header.length);
				networkWriter.writeInt(data.length);
				networkWriter.write(header);
				networkWriter.write(data);
				networkWriter.flush();
				this.frameID++;

				try {
					Thread.sleep(10);
				} catch (InterruptedException e) {}
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
		this.accDataList.add(new AccData((int)(currentUpdateTime-firstStartTime), sensor[0], sensor[1], sensor[2]));
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
}
