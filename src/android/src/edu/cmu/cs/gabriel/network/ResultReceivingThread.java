
package edu.cmu.cs.gabriel.network;

import java.io.DataInputStream;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.TreeMap;
import java.util.Vector;

import org.json.JSONException;
import org.json.JSONObject;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

public class ResultReceivingThread extends Thread {
	
	public static final String MESSAGE_CONTROL = "control";
	public static final String MESSAGE_RESULT = "result";
	public static final String MESSAGE_FRAME_ID = "id";
	
	private static final String LOG_TAG = "krha";
	
	private Handler networkHandler;
	private Handler tokenHandler;
	private DataInputStream networkReader;
	private boolean is_running = true;

	
	public ResultReceivingThread(DataInputStream dataInputStream, Handler networkHandler, Handler tokenHandler) {
		this.networkReader = dataInputStream;
		this.networkHandler = networkHandler;
		this.tokenHandler = tokenHandler;
	}

	@Override
	public void run() {
		// Recv initial simulation information
		while(is_running == true){			
			try {
				String recvMsg = this.receiveMsg(networkReader);
				this.notifyReceivedData(recvMsg);
			} catch (IOException e) {
				Log.e("krha", e.toString());
				// Do not send error to handler, Streaming thread already sent it.
//				this.notifyError(e.getMessage());				
				break;
			} catch (JSONException e) {
				Log.e("krha", e.toString());
				this.notifyError(e.getMessage());
			}
		}
	}

	private String receiveMsg(DataInputStream reader) throws IOException {
		int retLength = reader.readInt();
		byte[] recvByte = new byte[retLength];
		int readSize = 0;
		while(readSize < retLength){
			int ret = reader.read(recvByte, readSize, retLength-readSize);
			if(ret <= 0){
				break;
			}
			readSize += ret;
		}
		String receivedString = new String(recvByte);
		return receivedString;
	}
	
	private void notifyReceivedData(String recvData) throws JSONException {	
		// convert the message to JSON
		JSONObject obj;		
		String controlMsg = null, returnMsg = null;
		long frameID = -1;
		obj = new JSONObject(recvData);
		
		try{
			controlMsg = obj.getString(MESSAGE_CONTROL);
		} catch(JSONException e){}
		try{
			returnMsg = obj.getString(MESSAGE_RESULT);
		} catch(JSONException e){}
		try{
			frameID = obj.getLong(MESSAGE_FRAME_ID);
		} catch(JSONException e){}

		if (frameID != -1){
			Message msg = Message.obtain();
			msg.what = VideoStreamingThread.NETWORK_RET_TOKEN;
			Bundle data = new Bundle();
			data.putLong(ResultReceivingThread.MESSAGE_FRAME_ID, frameID);
			msg.setData(data);
			this.tokenHandler.sendMessage(msg);
		}			
		if (controlMsg != null){
			Message msg = Message.obtain();
			msg.what = VideoStreamingThread.NETWORK_RET_CONFIG;
			msg.obj = controlMsg;			
			this.networkHandler.sendMessage(msg);
		}
		if (returnMsg != null){
			Message msg = Message.obtain();
			Log.d(LOG_TAG, returnMsg);
			msg.what = VideoStreamingThread.NETWORK_RET_RESULT;
			msg.obj = returnMsg;			
			this.networkHandler.sendMessage(msg);			
		}
	}

	private void notifyError(String errorMessage) {		
		Message msg = Message.obtain();
		msg.what = VideoStreamingThread.NETWORK_RET_FAILED;
		msg.obj = errorMessage;
		this.networkHandler.sendMessage(msg);
	}
	
	public void close() {
		this.is_running = false;		
		try {
			if(this.networkReader != null)
				this.networkReader.close();
		} catch (IOException e) {
		}
	}
}
