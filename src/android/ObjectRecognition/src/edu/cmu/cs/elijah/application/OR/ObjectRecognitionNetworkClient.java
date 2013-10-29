package edu.cmu.cs.elijah.application.OR;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.ArrayList;

import android.app.ProgressDialog;
import android.content.Context;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.util.Log;

public class ObjectRecognitionNetworkClient extends Thread {
	public static final String TAG = "krha_app";
	public static final int FEEDBACK = 1;
	public static final int ERROR = 2;

	private Socket mClientSocket = null;
	private DataInputStream networkReader = null;
	private DataOutputStream networkWriter = null;

	private ObjectRecognitionActivity mActivity;
	private Context mContext;
	private Handler mHandler;
	private Looper mLoop;

	// time stamp
	protected long dataSendStart;
	protected long dataSendEnd;
	protected long dataReceiveStart;
	protected long dataReceiveEnd;
	private ArrayList<File> mFileList = new ArrayList<File>();
	private ArrayList<byte[]> mCapturedImages = new ArrayList<byte[]>();
	private String ip;
	private int port;

	public ObjectRecognitionNetworkClient(ObjectRecognitionActivity activity, Context context, Handler handler) {
		mActivity = activity;
		mContext = context;
		mHandler = handler;
	}

	public void initConnection(String ip, int port) {
		this.ip = ip;
		this.port = port;
	}

	public void run() {

		try {
			this.mClientSocket = new Socket();
			this.mClientSocket.connect(new InetSocketAddress(ip, port), 3 * 1000);
			this.networkWriter = new DataOutputStream(mClientSocket.getOutputStream());
			this.networkReader = new DataInputStream(mClientSocket.getInputStream());
		} catch (IOException e) {
			e.printStackTrace();
			this.notifyError(e.getMessage());
			return;
		}

		while (true) {
			if (mFileList.size() == 0 && mCapturedImages.size() == 0) {
				try {
					Thread.sleep(100);
				} catch (InterruptedException e) {
					e.printStackTrace();
				}
				continue;
			}

			// automated test
			long processStartTime = System.currentTimeMillis();
			while (mFileList.size() > 0) {
				File testFile = mFileList.remove(0);
				byte[] testImageData = new byte[(int) testFile.length()];
				try {
					FileInputStream fs = new FileInputStream(testFile);
					fs.read(testImageData, 0, testImageData.length);
					requestServer(testImageData, testFile.getName());
				} catch (FileNotFoundException e) {
					e.printStackTrace();
					this.notifyError(e.getMessage());
					return;					
				} catch (IOException e) {
					e.printStackTrace();
					this.notifyError(e.getMessage());
					return;					
				}
			}

			// handle single image
			while (mCapturedImages.size() > 0) {
				byte[] imageBytes = mCapturedImages.remove(0);
				try {
					requestServer(imageBytes, "camera");
				} catch (IOException e) {
					e.printStackTrace();
					this.notifyError(e.getMessage());
					return;
				}
			}
		}
	}

	private void notifyError(String message) {
		// callback
		Message msg = Message.obtain();
		msg.what = ObjectRecognitionNetworkClient.ERROR;
		Bundle data = new Bundle();
		data.putString("message", message);
		msg.setData(data);
		mHandler.sendMessage(msg);
	}

	private void requestServer(byte[] testImageData, String imageName) throws IOException {
		int totalSize = testImageData.length;
		// time stamp
		dataSendStart = System.currentTimeMillis();
		// upload image
		networkWriter.writeInt(totalSize);
		networkWriter.write(testImageData);
		networkWriter.flush(); // flush for accurate time measure

		int ret_size = networkReader.readInt();
		Log.d("krha", "ret data size : " + ret_size);
		byte[] ret_byte = new byte[ret_size];
		networkReader.read(ret_byte);
		String ret = new String(ret_byte, "UTF-8");

		// time stamp
		dataReceiveEnd = System.currentTimeMillis();
		if (ret.trim().length() == 0) {
			ret = "Nothing";
		}
		// String message = imageName + "\t" + dataSendStart + "\t" +
		// dataReceiveEnd + "\t" + (dataReceiveEnd-dataSendStart) + "\t" + ret;
		String message = ret;
		Log.d("krha_app", message);

		// callback
		Message msg = Message.obtain();
		msg.what = ObjectRecognitionNetworkClient.FEEDBACK;
		Bundle data = new Bundle();
		data.putString("message", message);
		msg.setData(data);
		mHandler.sendMessage(msg);

	}

	public void uploadImageList(ArrayList<File> imageList) {
		mFileList = imageList;
	}

	public void uploadImage(byte[] imageData) {
		this.mCapturedImages.add(imageData);
	}

	public void close() {
		// TODO Auto-generated method stub
		try {
			if (mClientSocket != null)
				mClientSocket.close();
			if (networkReader != null)
				networkReader.close();
			if (networkWriter != null)
				networkWriter.close();
		} catch (IOException e) {
			e.printStackTrace();
		}
	}
}
