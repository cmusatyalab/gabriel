
package edu.cmu.cs.gabriel.network;

import java.io.DataInputStream;
import java.io.DataOutputStream;

import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.TreeMap;
import java.util.Vector;

import org.json.JSONException;
import org.json.JSONObject;

import edu.cmu.cs.gabriel.token.TokenController;

import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;

public class ResultReceivingThread extends Thread {
	
	private static final String LOG_TAG = "krha";
	
	private InetAddress remoteIP;
	private int remotePort;
	private Socket tcpSocket;	
	private boolean is_running = true;
	private DataOutputStream networkWriter;
	private DataInputStream networkReader;
	
	private Handler returnMsgHandler;
	private TokenController tokenController;
	

	public ResultReceivingThread(String GABRIEL_IP, int port, Handler returnMsgHandler, TokenController tokenController) {
		is_running = false;
		this.tokenController = tokenController;
		this.returnMsgHandler = returnMsgHandler;
		try {
			remoteIP = InetAddress.getByName(GABRIEL_IP);
		} catch (UnknownHostException e) {
			Log.e(LOG_TAG, "unknown host: " + e.getMessage());
		}
		remotePort = port;
	}

	@Override
	public void run() {
		this.is_running = true;
		Log.i(LOG_TAG, "Result receiving thread running");

		try {
			tcpSocket = new Socket();
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			networkReader = new DataInputStream(tcpSocket.getInputStream());
		} catch (IOException e) {
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e.getMessage());
			this.notifyError(e.getMessage());
			this.is_running = false;
			return;
		}
		
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
		String returnMsg = null;
		int injectedToken = 0;
		long frameID = -1;
		obj = new JSONObject(recvData);
		
		try{
			returnMsg = obj.getString(NetworkProtocol.HEADER_MESSAGE_RESULT);
		} catch(JSONException e){}
		try{
			injectedToken = obj.getInt(NetworkProtocol.HEADER_MESSAGE_INJECT_TOKEN);
		} catch(JSONException e){}
		try{
			frameID = obj.getLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID);
		} catch(JSONException e){}

		if (frameID != -1){
			Message msg = Message.obtain();
			msg.what = NetworkProtocol.NETWORK_RET_TOKEN;
			Bundle data = new Bundle();
			data.putLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID, frameID);
			msg.setData(data);
			this.tokenController.tokenHandler.sendMessage(msg);
		}
		if (injectedToken > 0){
			this.tokenController.increaseTokens(injectedToken);
		}
	}

	private void notifyError(String errorMessage) {		
		Message msg = Message.obtain();
		msg.what = NetworkProtocol.NETWORK_RET_FAILED;
		msg.obj = errorMessage;
		this.returnMsgHandler.sendMessage(msg);
	}
	
	public void close() {
		this.is_running = false;
		try {
			if(this.networkReader != null)
				this.networkReader.close();
		} catch (IOException e) {
		}
		try {
			if(this.networkWriter != null)
				this.networkWriter.close();
		} catch (IOException e) {
		}
		try {
			if(this.tcpSocket != null)
				this.tcpSocket.close();
		} catch (IOException e) {
		}
	}
}
