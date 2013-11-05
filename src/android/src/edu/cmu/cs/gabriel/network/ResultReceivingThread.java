
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
	
	private static final String MESSAGE_CONTROL = "control";
	private static final String MESSAGE_RESULT = "result";
	private static final String MESSAGE_FRAME_ID = "id";
	
	private static final String LOG_TAG = "krha";
	
	private Handler mHandler;
	private DataInputStream networkReader;
	private boolean is_running = true;

	private TreeMap<Long, Long> receiver_stamps = new TreeMap<Long, Long>();
	private long frame_latency_diff = 0;
	private long frame_latency_count = 0;
	
	public ResultReceivingThread(DataInputStream dataInputStream, Handler mHandler) {
		this.networkReader = dataInputStream;
		this.mHandler = mHandler;
	}
	
	public TreeMap<Long, Long> getReceiverStamps(){
		return this.receiver_stamps;
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
		
		Message msg = Message.obtain();
		if (controlMsg != null){			
			msg.what = VideoStreamingThread.NETWORK_RET_CONFIG;
			msg.obj = controlMsg;
			this.mHandler.sendMessage(msg);
		}
		if (returnMsg != null){
			Log.d(LOG_TAG, returnMsg);
			msg.what = VideoStreamingThread.NETWORK_RET_RESULT;
			msg.obj = returnMsg;
			this.mHandler.sendMessage(msg);			
		}
		if (frameID != -1){
			Long sent_time = this.receiver_stamps.get(frameID);
			if (sent_time != null){
				long time_diff = System.currentTimeMillis() - sent_time;
				frame_latency_diff  += time_diff;
				frame_latency_count++;
				if (frame_latency_count % 10 == 0){
					Log.d(LOG_TAG, "average frame latency : " + frame_latency_diff/frame_latency_count);
				}				
			}
			if (this.receiver_stamps.size() > 30*60){
				this.receiver_stamps.clear();
			}
		}
	}

	private void notifyError(String errorMessage) {		
		Message msg = Message.obtain();
		msg.what = VideoStreamingThread.NETWORK_RET_FAILED;
		msg.obj = errorMessage;
		this.mHandler.sendMessage(msg);
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
