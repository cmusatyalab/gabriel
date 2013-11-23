package edu.cmu.cs.gabriel.network;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileDescriptor;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.FilenameFilter;
import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.TreeMap;
import java.util.Vector;

import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.token.SentPacketInfo;
import edu.cmu.cs.gabriel.token.TokenController;

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
	protected File[] imageFiles = null;
	protected int indexImageFile = 0;

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

	private TokenController tokenController;

	public VideoStreamingThread(FileDescriptor fd, String IPString, int port, Handler handler, TokenController tokenController) {
		is_running = false;
		this.networkHander = handler;
		this.tokenController = tokenController;
		
		try {
			remoteIP = InetAddress.getByName(IPString);
		} catch (UnknownHostException e) {
			Log.e(LOG_TAG, "unknown host: " + e.getMessage());
		}
		remotePort = port;
		cameraInputStream = new FileInputStream(fd);
		
		// check input data at image directory
		imageFiles = this.getImageFiles(Const.TEST_IMAGE_DIR);
	}

	private File[] getImageFiles(File imageDir) {
		if (imageDir == null){
			return null;
		}
	    File[] files = imageDir.listFiles(new FilenameFilter() {			
			@Override
			public boolean accept(File dir, String filename) {
				if (filename.toLowerCase().endsWith("jpg") == true)
					return true;
				if (filename.toLowerCase().endsWith("jpeg") == true)
					return true;
				return false;
			}
		});
		return files;
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
			networkReceiver = new VideoControlThread(networkReader, this.networkHander);
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
					this.frameID++;
					this.tokenController.sendData(this.frameID, data.length + header.length);
					this.tokenController.decreaseToken();
				}
				
			} catch (IOException e) {
				Log.e(LOG_TAG, e.getMessage());
				this.notifyError(e.getMessage());
				this.is_running = false;
				return;
			} 

			try{
				Thread.sleep(1);
			} catch (InterruptedException e) {}
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

	
	private Size cameraImageSize = null;
    private long frameCount = 0, firstUpdateTime = 0;
    private long prevUpdateTime = 0, currentUpdateTime = 0;
    private long totalsize = 0;
    
	public void push(byte[] frame, Parameters parameters) {
		if (this.tokenController.getCurrentToken() <= 0) {
			return;
		}		

        int datasize = 0;
        cameraImageSize = parameters.getPreviewSize();
        if (this.imageFiles == null){
            YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
            		cameraImageSize.height, null);
            ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
            image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 90, tmpBuffer);
            this.frameBufferList.add(tmpBuffer.toByteArray());
            datasize = tmpBuffer.size();
        }else{
        	try {
        		int index = indexImageFile % this.imageFiles.length;
	            datasize = (int) this.imageFiles[index].length();
				FileInputStream fi = new FileInputStream(this.imageFiles[index]);
				byte[] buffer = new byte[datasize];
				fi.read(buffer, 0, datasize);
	            this.frameBufferList.add(buffer);
	            indexImageFile++;
			} catch (FileNotFoundException e) {
				e.printStackTrace();
			} catch (IOException e) {
				e.printStackTrace();
			}
        }
		
        if (firstUpdateTime == 0) {
            firstUpdateTime = System.currentTimeMillis();
        }
        currentUpdateTime = System.currentTimeMillis();
        frameCount++;
        totalsize += datasize;
        if (frameCount % 10 == 0) {
        	Log.d(LOG_TAG, "(" + cameraImageSize.width + "," + cameraImageSize.height + ")" +
        			"BW: " + 8.0*totalsize / (currentUpdateTime-firstUpdateTime)/1000 + 
        			" Mbps\tCurrent FPS: " + 8.0*datasize/(currentUpdateTime - prevUpdateTime)/1000 + " Mbps\t" +
        			"FPS: " + 1000.0*frameCount/(currentUpdateTime-firstUpdateTime));
		}
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
