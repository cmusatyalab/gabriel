package edu.cmu.cs.gabriel.network;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.SocketException;
import java.net.UnknownHostException;
import java.util.Vector;

import android.nfc.Tag;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

public class VideoStreamingThread extends Thread {
	public static final int PROTOCOL_UDP = 0;
	public static final int PROTOCOL_TCP = PROTOCOL_UDP + 1;
	public static final int PROTOCOL_RTPUDP = PROTOCOL_TCP + 1;
	public static final int PROTOCOL_RTPTCP = PROTOCOL_RTPUDP + 1;
	
	public static final int NETWORK_RET_FAILED = 1;
	public static final int NETWORK_RET_RESULT = 2;
	public static final int NETWORK_RET_CONFIG = 3;

	private static final String LOG_TAG = "krha";

	static final int BUFFER_SIZE = 102400; // need to negotiate with server
	private boolean is_running = false;

	private int protocolIndex; // may use a protocol other than UDP
	private InetAddress remoteIP;
	private int remotePort;

	// UDP
	private DatagramSocket udpSocket = null;
	// TCP
	private Socket tcpSocket = null;
	private DataOutputStream networkWriter = null;
	private ResultReceivingThread networkReceiver = null;

	private FileInputStream cameraInputStream;
	private Vector<byte[]> frameBufferList = new Vector<byte[]>();
	private Handler networkHander = null;

	public VideoStreamingThread(int protocol, FileDescriptor fd, String IPString, int port, Handler handler) {
		is_running = false;
		this.networkHander  = handler;
		try {
			remoteIP = InetAddress.getByName(IPString);
		} catch (UnknownHostException e) {
			Log.e(LOG_TAG, "unknown host: " + e.getMessage());
		}
		remotePort = port;

		this.protocolIndex = protocol;
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
			switch (protocolIndex) {
			case PROTOCOL_UDP:
				udpSocket = new DatagramSocket();
				udpSocket.setReceiveBufferSize(BUFFER_SIZE);
				udpSocket.setSendBufferSize(BUFFER_SIZE);
				udpSocket.connect(remoteIP, remotePort);
				Log.i(LOG_TAG, "Streaming channel connected to: " + udpSocket.getInetAddress().toString() + ":"
						+ udpSocket.getPort());
				break;
			case PROTOCOL_TCP:
				tcpSocket = new Socket();
				tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
				networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
				DataInputStream networkReader = new DataInputStream(tcpSocket.getInputStream());
				networkReceiver = new ResultReceivingThread(networkReader, this.networkHander);
				networkReceiver.start();
				break;
			}
		} catch (IOException e) {
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e.getMessage());
			this.notifyError(e.getMessage());
			this.is_running = false;
			return;
		}

		while (this.is_running) {
			switch (protocolIndex) {
			case PROTOCOL_UDP:
				try {
					bytes_read = cameraInputStream.read(buffer, 0, BUFFER_SIZE);
					Log.v(LOG_TAG, "Read " + bytes_read + " bytes");
				} catch (IOException e) {
					Log.e(LOG_TAG, e.getMessage());
					this.notifyError(e.getMessage());
				}

				if (bytes_read <= 0) {
					try {
						Thread.sleep(10);
					} catch (InterruptedException e) {}
				} else {
					DatagramPacket packet = new DatagramPacket(buffer, bytes_read);
					try {
						udpSocket.send(packet);
					} catch (IOException e) {
						Log.e(LOG_TAG, e.getMessage());
						this.notifyError(e.getMessage());
						this.is_running = false;
						return;
					}
				}
				bytes_count += bytes_read;
				packet_count++;
				break;
			case PROTOCOL_TCP:
				try {
					while (this.frameBufferList.size() > 0){
						byte[] data = this.frameBufferList.remove(0);
						networkWriter.writeInt(data.length);
						networkWriter.write(data, 0, data.length);
						networkWriter.flush();						
					}
				} catch (IOException e) {
					Log.e(LOG_TAG, e.getMessage());
					this.notifyError(e.getMessage());
					this.is_running = false;
					return;
				}
				break;
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
			} catch (IOException e) {}
		}
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

	public void push(byte[] byteArray) {
		if (this.frameBufferList.size() > 5){
			this.frameBufferList.remove(0);
		}
		this.frameBufferList.add(byteArray);
	}
	
	private void notifyError(String message) {
		// callback
		Message msg = Message.obtain();
		msg.what = VideoStreamingThread.NETWORK_RET_FAILED;
		Bundle data = new Bundle();
		data.putString("message", message);
		msg.setData(data);		
		this.networkHander.sendMessage(msg);
	}
}
