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

public class VideoStreamingThread extends Thread {

	private static final String LOG_TAG = "krha";

	private static final int MAX_TOKEN_SIZE = 2;
	private int currentToken = MAX_TOKEN_SIZE;

	private int protocolIndex; // may use a protocol other than UDP
	static final int BUFFER_SIZE = 102400; // only for the UDP case
	private boolean is_running = false;
	private InetAddress remoteIP;
	private int remotePort;

	// UDP
	private DatagramSocket udpSocket = null;
	// TCP
	private Socket tcpSocket = null;
	private DataOutputStream networkWriter = null;
	private VideoControlThread networkReceiver = null;

	private FileInputStream cameraInputStream;
	private Vector<byte[]> frameBufferList = new Vector<byte[]>();
	private Handler networkHander = null;
	private long frameID = 0;
	private TreeMap<Long, SentPacketInfo> latencyStamps = new TreeMap<Long, SentPacketInfo>();

	class SentPacketInfo {
		public long sentTime;
		public int sentSize;

		public SentPacketInfo(long currentTimeMillis, int sentSize) {
			this.sentTime = currentTimeMillis;
			this.sentSize = sentSize;
		}
	}

	public VideoStreamingThread(FileDescriptor fd, String IPString, int port, Handler handler) {
		is_running = false;
		this.networkHander = handler;
		try {
			remoteIP = InetAddress.getByName(IPString);
		} catch (UnknownHostException e) {
			Log.e(LOG_TAG, "unknown host: " + e.getMessage());
		}
		remotePort = port;

		cameraInputStream = new FileInputStream(fd);
	}

	public void run() {
		this.is_running = true;
		Log.i(LOG_TAG, "Streaming thread running");

		byte[] buffer = new byte[BUFFER_SIZE];
		int bytes_read = 0;
		int bytes_count = 0;
		int packet_count = 0;

		try {
			tcpSocket = new Socket();
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			DataInputStream networkReader = new DataInputStream(tcpSocket.getInputStream());
			networkReceiver = new VideoControlThread(networkReader, this.networkHander, this.tokenHandler);
			networkReceiver.start();
		} catch (IOException e) {
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e.getMessage());
			this.notifyError(e.getMessage());
			this.is_running = false;
			return;
		}

		while (this.is_running) {
			try {
				while (this.frameBufferList.size() > 0) {
					byte[] header = ("{\"id\":" + this.frameID + "}").getBytes();
					byte[] data = this.frameBufferList.remove(0);
					networkWriter.writeInt(header.length);
					networkWriter.writeInt(data.length);
					networkWriter.write(header);
					networkWriter.write(data, 0, data.length);
					networkWriter.flush();
					latencyStamps.put(this.frameID, new SentPacketInfo(System.currentTimeMillis(), data.length
							+ header.length));
					this.frameID++;
					if (this.currentToken > 0) {
						this.currentToken--;
					}
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
		if (udpSocket != null) {
			udpSocket.close();
			udpSocket = null;
		}
		if (cameraInputStream != null) {
			try {
				cameraInputStream.close();
			} catch (IOException e) {
			}
		}
		if (tcpSocket != null) {
			try {
				tcpSocket.close();
			} catch (IOException e) {
			}
		}
		if (networkWriter != null) {
			try {
				networkWriter.close();
			} catch (IOException e) {
			}
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

	public void push(byte[] frame, Parameters parameters) {

		if (firstStartTime == 0) {
			firstStartTime = System.currentTimeMillis();
		}
		currentUpdateTime = System.currentTimeMillis();
		sentframeCount++;

		if (currentToken > 0) {
			cameraImageSize = parameters.getPreviewSize();
			YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
					cameraImageSize.height, null);
			ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
			image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 95, tmpBuffer);
			this.frameBufferList.add(tmpBuffer.toByteArray());
			prevUpdateTime = currentUpdateTime;
		}
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
		private long frame_latency_count = 0;;

		public void handleMessage(Message msg) {
			if (msg.what == NetworkProtocol.NETWORK_RET_TOKEN) {
				Bundle bundle = msg.getData();
				long recvFrameID = bundle.getLong(VideoControlThread.MESSAGE_FRAME_ID);
				SentPacketInfo sentPacket = latencyStamps.remove(recvFrameID);
				if (sentPacket != null) {
					long time_diff = System.currentTimeMillis() - sentPacket.sentTime;
					frame_latency += time_diff;
					frame_size += sentPacket.sentSize;
					frame_latency_count++;
					if (frame_latency_count % 100 == 0) {
						Log.d(LOG_TAG, cameraImageSize.width + "x" + cameraImageSize.height + " " + "Latency : "
								+ frame_latency / frame_latency_count + " (ms)\tThroughput : " + frame_size
								/ frame_latency_count + " (Bps)");
					}
				}
				if (latencyStamps.size() > 30 * 60) {
					latencyStamps.clear();
				}

				increaseToken();
			}
		}
	};

	public int getCurrentToken() {
		return this.currentToken;
	}

	public void increaseToken() {
		this.currentToken++;
	}
}
