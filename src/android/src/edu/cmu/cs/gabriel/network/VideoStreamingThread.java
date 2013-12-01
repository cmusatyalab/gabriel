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
import edu.cmu.cs.gabriel.network.AccStreamingThread.AccData;
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
	private byte[] frameBuffer = null;
	private long frameGeneratedTime = 0;
	private Object frameLock = new Object();
	private Handler networkHander = null;
	private long sequenceID = 1;	// must start from 1

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

		int packet_count = 0;
		long packet_firstUpdateTime = 0;
		long packet_currentUpdateTime = 0;
		int packet_totalsize = 0;
		long packet_prevUpdateTime = 0;

		try {
			tcpSocket = new Socket();
			tcpSocket.setTcpNoDelay(true);
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			DataInputStream networkReader = new DataInputStream(tcpSocket.getInputStream());
			networkReceiver = new VideoControlThread(networkReader, this.networkHander, tokenController);
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
				// check token
				if (this.tokenController.getCurrentToken() <= 0) {
					Log.d(LOG_TAG, "waiting");
					continue;
				}
				
				// get data
				byte[] data = null;
				long dataTime = 0;
				synchronized(frameLock){
					while (this.frameBuffer == null){
						try {
							frameLock.wait();
						} catch (InterruptedException e) {}						
					}
					
					data = this.frameBuffer;
					dataTime = this.frameGeneratedTime;
					this.frameBuffer = null;			
				}
				
				// make it as a single packet
				ByteArrayOutputStream baos = new ByteArrayOutputStream();
		        DataOutputStream dos=new DataOutputStream(baos);
				byte[] header = ("{\"id\":" + this.sequenceID + "}").getBytes();
				dos.writeInt(header.length);
				dos.writeInt(data.length);
				dos.write(header);
				dos.write(data);

				this.tokenController.sendData(this.sequenceID, System.currentTimeMillis(), dos.size());	
				networkWriter.write(baos.toByteArray());
				networkWriter.flush();
				this.tokenController.decreaseToken();
				this.sequenceID++;
				
				// measurement
		        if (packet_firstUpdateTime == 0) {
		        	packet_firstUpdateTime = System.currentTimeMillis();
		        }
		        packet_currentUpdateTime = System.currentTimeMillis();
		        packet_count++;
		        packet_totalsize += data.length;
		        if (packet_count % 10 == 0) {
		        	Log.d(LOG_TAG, "(NET)\t" + "BW: " + 8.0*packet_totalsize / (packet_currentUpdateTime-packet_firstUpdateTime)/1000 + 
		        			" Mbps\tCurrent FPS: " + 8.0*data.length/(packet_currentUpdateTime - packet_prevUpdateTime)/1000 + " Mbps\t" +
		        			"FPS: " + 1000.0*packet_count/(packet_currentUpdateTime-packet_firstUpdateTime));
				}
		        packet_prevUpdateTime = packet_currentUpdateTime;
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
    private long frame_count = 0, frame_firstUpdateTime = 0;
    private long frame_prevUpdateTime = 0, frame_currentUpdateTime = 0;
    private long frame_totalsize = 0;
    
	public void push(byte[] frame, Parameters parameters) {
        int datasize = 0;
        cameraImageSize = parameters.getPreviewSize();
        if (this.imageFiles == null){
            YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
            		cameraImageSize.height, null);
            ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
            image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 90, tmpBuffer);
            synchronized (frameLock) {
                this.frameBuffer = tmpBuffer.toByteArray();
                this.frameGeneratedTime = System.currentTimeMillis();
                frameLock.notify();
			}
            datasize = tmpBuffer.size();
        }else{
        	try {
        		int index = indexImageFile % this.imageFiles.length;
	            datasize = (int) this.imageFiles[index].length();
				FileInputStream fi = new FileInputStream(this.imageFiles[index]);
				byte[] buffer = new byte[datasize];
				fi.read(buffer, 0, datasize);
	            synchronized (frameLock) {
		            this.frameBuffer = buffer;
	                this.frameGeneratedTime = System.currentTimeMillis();
	                frameLock.notify();
	            }
	            indexImageFile++;
			} catch (FileNotFoundException e) {
			} catch (IOException e) {
			}
        }
		
        if (frame_firstUpdateTime == 0) {
            frame_firstUpdateTime = System.currentTimeMillis();
        }
        frame_currentUpdateTime = System.currentTimeMillis();
        frame_count++;
        frame_totalsize += datasize;
    	/*
        if (frame_count % 50 == 0) {
        	Log.d(LOG_TAG, "(IMG)\t" +
        			"BW: " + 8.0*frame_totalsize / (frame_currentUpdateTime-frame_firstUpdateTime)/1000 + 
        			" Mbps\tCurrent FPS: " + 8.0*datasize/(frame_currentUpdateTime - frame_prevUpdateTime)/1000 + " Mbps\t" +
        			"FPS: " + 1000.0*frame_count/(frame_currentUpdateTime-frame_firstUpdateTime));
		}
		*/
        frame_prevUpdateTime = frame_currentUpdateTime;
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
