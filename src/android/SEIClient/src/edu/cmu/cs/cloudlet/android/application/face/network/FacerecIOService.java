/**
* Copyright 2011 Carnegie Mellon University
*
* This material is being created with funding and support by the Department of Defense under Contract No. FA8721-05-C-0003 
* with Carnegie Mellon University for the operation of the Software Engineering Institute, a federally funded research and 
* development center.  As such, it is considered an externally sponsored project  under Carnegie Mellon University's 
* Intellectual Property Policy.
*
* This material may not be released outside of Carnegie Mellon University without first contacting permission@sei.cmu.edu.
*
* This material makes use of the following Third-Party Software and Libraries which are used pursuant to the referenced 
* Licenses.  Any modification of Third-Party Software or Libraries must be in compliance with the applicable license 
* (and only if permitted):
* 
*    Android
*    Source: http://source.android.com/source/index.html
*    License: http://source.android.com/source/licenses.html
* 
*    CherryPy
*    Source: http://cherrypy.org/
*    License: https://bitbucket.org/cherrypy/cherrypy/src/697c7af588b8/cherrypy/LICENSE.txt
*
* Unless otherwise stated in any Third-Party License or as otherwise required by applicable law or agreed to in writing, 
* All Third-Party Software is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express 
* or implied.
*/

package edu.cmu.cs.cloudlet.android.application.face.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.Socket;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;

import edu.cmu.cs.cloudlet.android.application.face.ui.CloudletVMInfo;


import android.app.Service;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.SharedPreferences.OnSharedPreferenceChangeListener;
import android.graphics.ImageFormat;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.os.Binder;
import android.os.Environment;
import android.os.IBinder;
import android.util.Log;

public class FacerecIOService extends Service implements
		OnSharedPreferenceChangeListener {

	
	/** This is the file to which CloudletClientApp writes the VM info once it has successfully received a valid JSON response from the HTTP server. 
	 * The face rec app should read this file to get the VM info (ip, port of the fac rec server)  */
	public static final String CLOUDLET_VM_INFO_FILE  = Environment.getExternalStorageDirectory() + "/Cloudlet/" + "config/vm_info.json";
	
	protected CloudletVMInfo cloudletInfo; 
	
	/**
	 * Hint to the JPEG compressor, 0-100. 0 meaning compress for small size,
	 * 100 meaning compress for max quality.
	 */
	public static final int JPEG_COMPRESSION_HINT = 50;

	// defined in commonSupport.h on the server side
	public static final int MESSAGE_TYPE_JPEG_IMAGE = 1;
	public static final int MESSAGE_TYPE_IMAGE_REPONSE = 2;
	public static final int MESSAGE_START_TRAIN_MODE_REQUEST = 3;
	public static final int MESSAGE_START_TRAIN_MODE_RESPONSE = 4;
	public static final int MESSAGE_END_TRAIN_MODE_REQUEST = 5;
	public static final int MESSAGE_END_TRAIN_MODE_RESPONSE = 6;
	public static final int MESSAGE_TRAINING_COLLECTION_UPDATE   =7;

	public static final String LOG_TAG = "FacerecIOService";
	private final IBinder mfacrecBinder = new FacerecLocalBinder();
	protected ConcurrentHashMap<String, IFacerecClientDataListener> reigsteredCoTDataListners = new ConcurrentHashMap<String, IFacerecClientDataListener>();

	private Object lock = new Object();
	public static final int IMAGE_QUEUE_SEND_SIZE = 1;
	private ConcurrentLinkedQueue<RawPreviewImageInfo> imageSendQueue = new ConcurrentLinkedQueue<RawPreviewImageInfo>();

	private Socket socket;
	private DataInputStream socketDataInputStream;
	private DataOutputStream socketDataOutputStream;

	private SocketDataSendReceiveThread socketDataSendReceiveThread;

	//public static final String IP_ADDRESS = "192.168.1.111";
	//public static final String IP_ADDRESS = "192.168.168.230";
	//public static final String IP_ADDRESS = "192.168.168.222";	
	//public static final int TCP_SERVER_PORT = 9876;
	
	
	protected String serverIPAddress; 
	protected int serverPort; 
	
	private boolean startTraining = false;
	private boolean stopTraining = false; 
	private String trainingPersonName; 


	/*
	 * A local binder that is used to set the Listener instance handler. This
	 * "listener" instance is used to make callbacks to the calling activity
	 * (that has the Local binder)
	 */
	public class FacerecLocalBinder extends Binder implements
			IFacerecClientDataProvider {

		FacerecIOService getService() {
			return FacerecIOService.this;
		}

		public void registerActivityCallback(String className,
				IFacerecClientDataListener activityCallback) {
			reigsteredCoTDataListners.put(className, activityCallback);
		}

		public void unregisterActivityCallback(String className) {
			reigsteredCoTDataListners.remove(className);
		}
		

		/*
		 * This is called by the preview activity to send camera data to the
		 * service
		 */
		public void sendImageData(RawPreviewImageInfo rawImage) {

			/*
			 * Eventhough imageSendQueue.size() and imageSendQueue.add() are
			 * thread-safe independently we need to take a lock to make both
			 * operations together atomic.
			 */
			synchronized (lock) {
				// only add the raw data to the queue if the queue has some
				// space.
				// This is a simple way to drop frames
				if (rawImage != null
						&& imageSendQueue.size() < IMAGE_QUEUE_SEND_SIZE) {

					// add the raw image data to the queue so that
					// ImageSenderThread can
					// remove from this queue, process the raw data and send it
					// over the network
					/*Log.d(LOG_TAG,
							"Adding image to the send queue that size "
									+ imageSendQueue.size()); */

					imageSendQueue.add(rawImage);

				}
			}
		}
		
		@Override
		public void startTraining(String personName) {
			startTraining = true; 
			trainingPersonName = personName;			
		}
		
		@Override
		public void stopTraining() {
			stopTraining = true; 			
		}
	}

	@Override
	public IBinder onBind(Intent intent) {
		Log.d(LOG_TAG, "Serivce onBind() method called. ");

		return mfacrecBinder;
	}

	@Override
	public boolean onUnbind(Intent intent) {
		Log.d(LOG_TAG, "Service onUnbind() method called. ");
		return super.onUnbind(intent);
	}

	@Override
	public void onCreate() {
		super.onCreate();
		loadVMServerInfo();		
		// krha
//		serverIPAddress = CloudletActivity.SYNTHESIS_SERVER_IP;
//		serverPort = CloudletActivity.TEST_CLOUDLET_APP_FACE_PORT;		
		initSocketAndThreads();
	}

	@Override
	public void onDestroy() {
		super.onDestroy();

		socketDataSendReceiveThread.requestStop();
		doSocketCleanup();
	}

	private void loadVMServerInfo() {
		// read the IP and port from the file
		cloudletInfo = new CloudletVMInfo();
		if (cloudletInfo.loadFromFile(CLOUDLET_VM_INFO_FILE)) {

			serverIPAddress = cloudletInfo.getIpAddress();
			serverPort = cloudletInfo.getPort();
			
			Log.i(LOG_TAG, "Loaded from " + CLOUDLET_VM_INFO_FILE + " IP: ["
					+ serverIPAddress + "] port: [" + serverPort + "]");

		} else {
			Log.e(LOG_TAG, "Error loading VM info from file  "
					+ CLOUDLET_VM_INFO_FILE);
		}

	}
	
	private void initSocket() {
		try {
			
			Log.d(LOG_TAG, "Trying to open a TCP connection to " + this.serverIPAddress
					+ " port " + this.serverPort);
			//socket = new Socket(IP_ADDRESS, TCP_SERVER_PORT);
			socket = new Socket(this.serverIPAddress, this.serverPort);

			socket.setTcpNoDelay(true);
			//socket.setKeepAlive(true);
			socket.setSendBufferSize(1024*100);
			Log.d(LOG_TAG, "Successfully opened a TCP connection to IP ->" + this.serverIPAddress
					+ ",  PORT -> " + this.serverPort);
		} catch (UnknownHostException e) {
			e.printStackTrace();
			Log.e(LOG_TAG, "Cannot Connect to IP ->" + this.serverIPAddress + ",  PORT -> " + this.serverPort);
		} catch (IOException e) {
			e.printStackTrace();
			Log.e(LOG_TAG, "Cannot Connect to IP ->" + this.serverIPAddress + ",  PORT -> " + this.serverPort);
		} catch (Exception e) {
			e.printStackTrace();
			Log.e(LOG_TAG, "Cannot Connect to IP ->" + this.serverIPAddress + ",  PORT -> " + this.serverPort);
		}
		

		// associated the input and output streams
		if (socket != null && socket.isConnected() && !socket.isInputShutdown()
				&& !socket.isOutputShutdown()) {
			Log.d(LOG_TAG, "Successfully opened a TCP connection to IP ->" + this.serverIPAddress
					+ ",  PORT -> " + this.serverPort);
			
			try {
				socketDataInputStream = new DataInputStream(
						socket.getInputStream());
				socketDataOutputStream = new DataOutputStream(
						socket.getOutputStream());
			} catch (IOException e) {
				e.printStackTrace();
			}
		}

		// TODO - problem connecting to the socket
	}

	private void doSocketCleanup() {
		try {

			if (socketDataInputStream != null)
				socketDataInputStream.close();

			if (socketDataOutputStream != null)
				socketDataOutputStream.close();

			if (socket != null) {
				socket.close();
			}
		} catch (IOException ioe) {
			ioe.printStackTrace();
		}

	}

	private void initSocketAndThreads() {

		initSocket();

		// init the threads
		socketDataSendReceiveThread = new SocketDataSendReceiveThread();
		socketDataSendReceiveThread.start();
	}

	class SocketDataSendReceiveThread extends Thread {
		private volatile boolean stop = false;

		public void run() {
			
			while (!stop) {

				if (socket != null && !socket.isConnected()) {
					doSocketCleanup();
					initSocket();
				}

				/* First check if we need to send a training request */
				if (FacerecIOService.this.startTraining) {
					if (socketDataOutputStream != null) {
						// send request
						sendStartTrainingRequest();

						// wait for a response before sending another request
						handleResponse();
					}
				}

				else if (FacerecIOService.this.stopTraining) {

					if (socketDataOutputStream != null) {
						// send request
						sendStopTrainingRequest();

						// wait for a response before sending another request
						handleResponse();
					}
				}

				RawPreviewImageInfo rawImageInfo = null;
				/*
				 * remove() is a thread safe operation. However, we still need a
				 * synchronized block here because we were taking a lock while
				 * adding to the queue.
				 */
				synchronized (lock) {
					if (!imageSendQueue.isEmpty())
						rawImageInfo = imageSendQueue.remove();
					else
						continue;
				}

				if (rawImageInfo == null)
					continue;

				YuvImage yuvimage = new YuvImage(rawImageInfo.imageData,
						ImageFormat.NV21, rawImageInfo.width,
						rawImageInfo.height, null);
				ByteArrayOutputStream bos = new ByteArrayOutputStream();
				long start = System.currentTimeMillis();
				boolean result = yuvimage.compressToJpeg(new Rect(0, 0,
						rawImageInfo.width, rawImageInfo.height),
						JPEG_COMPRESSION_HINT, bos);
				long end = System.currentTimeMillis();
				
				//Log.e(LOG_TAG, "Image compression took: " + (end-start) + " ms.");
				byte[] jpegImageBytes = bos.toByteArray();

				if (socketDataOutputStream != null) {

					// send request
					sendImageRequest(jpegImageBytes);

					// wait for a response before sending another request
					handleResponse();

				}
			}
		}

		public synchronized void requestStop() {
			stop = true;
		}

	}	
	
	private void sendStartTrainingRequest() {
		try {

			//NULL terminate the string before sending it on the network 
			ByteBuffer byteBuf = ByteBuffer.wrap((trainingPersonName + '\0')
					.getBytes());
			byteBuf.order(ByteOrder.BIG_ENDIAN);

			// header
			socketDataOutputStream.writeInt(MESSAGE_START_TRAIN_MODE_REQUEST);
			//Adding 1 for the NULL termination character in the end
			socketDataOutputStream
					.writeInt(trainingPersonName.getBytes().length + 1);
			socketDataOutputStream.write(byteBuf.array());
		} catch (IOException ioe) {
			ioe.printStackTrace();
		}

		Log.e(LOG_TAG, "Training Mode START request send with name: "
				+ trainingPersonName);
		// TODO take a lock before changing this. 
		FacerecIOService.this.startTraining = false; 
	}
	
	
	private void sendStopTrainingRequest() {
		
		if(trainingPersonName == null || trainingPersonName.trim().length() ==0 )
			return; //ignore the stop training request in case there is no name set.
		
		try {
			socketDataOutputStream.writeInt(MESSAGE_END_TRAIN_MODE_REQUEST);
			socketDataOutputStream
					.writeInt(trainingPersonName.getBytes().length);

			ByteBuffer byteBuf = ByteBuffer.wrap(trainingPersonName.getBytes());
			byteBuf.order(ByteOrder.BIG_ENDIAN);
			socketDataOutputStream.write(byteBuf.array());
		} catch (IOException ioe) {
			ioe.printStackTrace();
		}

		Log.e(LOG_TAG, "Training Mode END request send with name: "
				+ trainingPersonName);
		FacerecIOService.this.stopTraining = false; // TODO take a lock before
													// changing this.

	}


	private void sendImageRequest(byte[] jpegImageBytes) {

		try {
			// IMP: Write all the data in Network Byte Order (Big Endian)
			// write message type. Since we are using a DataOutputStream
			// "writeInt"
			// will write "int" in Network Byte Order
			socketDataOutputStream.writeInt(MESSAGE_TYPE_JPEG_IMAGE);

			// write message length
			socketDataOutputStream.writeInt(jpegImageBytes.length);

			// We use a ByteBuffer make sure the image bytes are written in
			// network
			// byte order.
			ByteBuffer byteBuf = ByteBuffer.wrap(jpegImageBytes);
			byteBuf.order(ByteOrder.BIG_ENDIAN);
			socketDataOutputStream.write(byteBuf.array());

			Log.e(LOG_TAG,
					"Send an message (MESSAGE_TYPE_JPEG_IMAGE) with size "
							+ jpegImageBytes.length);
		} catch (IOException ioe) {
			ioe.printStackTrace();
		}

	}

	private void handleResponse()  {
		
		try
		{
		int messageType = socketDataInputStream.readInt();
		int messageSize = socketDataInputStream.readInt();
		
		if(messageSize>1000)
			return;

		switch (messageType) {
		case MESSAGE_TYPE_IMAGE_REPONSE:
			
			
			if( messageSize <= 36 )
			{
				Log.e(LOG_TAG, "ERROR: Got an invalid image message response. Ignoring it.");
				return;
			}
			
			
			ImageResponseMessage responseMsg = new ImageResponseMessage();

			Log.d(LOG_TAG, "Got response message type: " + messageType
					+ " and size: " + messageSize);

			responseMsg.detectTimeInMs = socketDataInputStream.readInt();
			responseMsg.objectsFound = socketDataInputStream.readInt();
			responseMsg.drawRect = socketDataInputStream.readInt();
			responseMsg.havePerson = socketDataInputStream.readInt();
			// read the rectangle
			FacerecRect rect = new FacerecRect();
			rect.x = socketDataInputStream.readInt();
			rect.y = socketDataInputStream.readInt();
			rect.width = socketDataInputStream.readInt();
			rect.height = socketDataInputStream.readInt();
			responseMsg.faceRect = rect;

			responseMsg.confidence = socketDataInputStream.readFloat();
			
			byte[] buffer = new byte[messageSize - 36]; // 36=9 X 4 bytes
			socketDataInputStream.read(buffer);
			responseMsg.name = byteToCharArray(buffer);

			Log.e(LOG_TAG,
					"Got Response Image Message String " + responseMsg.toString());

			//currently we are sending all the messages to the UI thread - even if they not valid i.e., don't have any rectangles/names etc in them
				for (String listnerKey : reigsteredCoTDataListners.keySet()) {
					IFacerecClientDataListener currentListner = reigsteredCoTDataListners
							.get(listnerKey);
					currentListner.updateImageResponseMessage(responseMsg);
				}
			

			break;

		case MESSAGE_START_TRAIN_MODE_RESPONSE:
			byte[] buf = new byte[messageSize]; // There is only one field in the response - name of person that was sent in the request
			socketDataInputStream.read(buf);

			Log.e(LOG_TAG,
					"Got Training START Response  " + new String(buf));
			
			break;

		case MESSAGE_END_TRAIN_MODE_RESPONSE:
			byte[] buf2 = new byte[messageSize]; // There is only one field in the response - name of person that was sent in the request
			socketDataInputStream.read(buf2);

			Log.e(LOG_TAG,
					"Got Training END Response  " + new String(buf2));
			
			break;

		default:
			// shouldn't happen
			break;
		}
		}catch(IOException ioe)
		{
			ioe.printStackTrace();
		}

	}

	
	private String byteToCharArray(byte[] buffer) {
		
		//The input buffer is null terminated and hence the we need to reduce the size by 1
		char[] charBuf = new char[buffer.length-1];
		for (int i=0; i<buffer.length-1; i++) {
			charBuf[i] = (char) buffer[i];
		}

		return new String(charBuf);
	}

	@Override
	public void onSharedPreferenceChanged(SharedPreferences arg0, String arg1) {
		// TODO Auto-generated method stub

	}

}


