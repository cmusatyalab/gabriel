
package edu.cmu.cs.gabriel.network;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Timer;

import org.json.JSONException;
import org.json.JSONObject;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Message;
import android.util.Base64;
import android.util.Log;
import edu.cmu.cs.gabriel.token.ReceivedPacketInfo;
import edu.cmu.cs.gabriel.token.TokenController;

public class ResultReceivingThread extends Thread {
	
	private static final String LOG_TAG = "ResultThread";
	
	private InetAddress remoteIP;
	private int remotePort;
	private Socket tcpSocket;	
	private boolean is_running = true;
	private DataOutputStream networkWriter;
	private DataInputStream networkReader;
	
	private Handler returnMsgHandler;
	private TokenController tokenController;
	private Timer timer = null;
	

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
			tcpSocket.setTcpNoDelay(true);
			tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
			networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
			networkReader = new DataInputStream(tcpSocket.getInputStream());
		} catch (IOException e) {
		    Log.e(LOG_TAG, Log.getStackTraceString(e));
			Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
			this.notifyError(e.getMessage());
			this.is_running = false;
			return;
		}
		
		// Recv initial simulation information
		while(is_running == true){			
			try {
				String recvMsg = this.receiveMsg(networkReader);
				//Log.v(LOG_TAG, recvMsg);
				this.notifyReceivedData(recvMsg);
			} catch (IOException e) {
				Log.e(LOG_TAG, e.toString());
				// Do not send error to handler, Streaming thread already sent it.
				this.notifyError(e.getMessage());				
				break;
			} catch (JSONException e) {
				Log.e(LOG_TAG, e.toString());
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
		Log.i(LOG_TAG, "aaa:" + System.currentTimeMillis());
		JSONObject recvJSON = new JSONObject(recvData);
		Log.i(LOG_TAG, "bbb:" + System.currentTimeMillis());
		String result = null;
		int injectedToken = 0;
		String engineID = "";
		long frameID = -1;
		
		try{
			result = recvJSON.getString(NetworkProtocol.HEADER_MESSAGE_RESULT);
		} catch (JSONException e) {}
		try {
			injectedToken = recvJSON.getInt(NetworkProtocol.HEADER_MESSAGE_INJECT_TOKEN);
		} catch (JSONException e) {}
		try {
			frameID = recvJSON.getLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID);
		} catch (JSONException e) {}
		try {
            engineID = recvJSON.getString(NetworkProtocol.HEADER_MESSAGE_ENGINE_ID);
		} catch (JSONException e) {}
		
		/* refilling tokens */
		if (frameID == -1) return;
		
            
        
//        if (injectedToken > 0){
//            this.tokenController.increaseTokens(injectedToken);
//        }
		
		if (result != null){
		    //Log.i(LOG_TAG, "Received result:" + result);
		    /* parsing result */
		    JSONObject resultJSON = new JSONObject(result);         
            String speechFeedback = "";
            Bitmap imageFeedback = null;
            
            /* general message */
            try {
            	
            	String status = resultJSON.getString("status");
            	Log.i(LOG_TAG, "ccc:" + status + ", " + System.currentTimeMillis());
            	
            	Message msg = Message.obtain();
    			msg.what = NetworkProtocol.NETWORK_RET_MESSAGE;
    			msg.obj = new ReceivedPacketInfo(frameID, engineID, status);
    			this.returnMsgHandler.sendMessage(msg);
            	
//            	if (!status.equals("success")) {
            		msg = Message.obtain();
        			msg.what = NetworkProtocol.NETWORK_RET_DONE;
        			this.returnMsgHandler.sendMessage(msg);
//            		return;
//            	}
            } catch (JSONException e) {
                Log.e(LOG_TAG, "the return message has no status field");
                return;
            }
            
            /* image guidance */
            try {
	            String imageFeedbackString = resultJSON.getString("image");
	            byte[] data = Base64.decode(imageFeedbackString.getBytes(), Base64.DEFAULT);
	            imageFeedback = BitmapFactory.decodeByteArray(data,0,data.length); 
	            
				Message msg = Message.obtain();
		        msg.what = NetworkProtocol.NETWORK_RET_IMAGE;
		        msg.obj = imageFeedback;
		        this.returnMsgHandler.sendMessage(msg);
            } catch (JSONException e) {
                Log.v(LOG_TAG, "no image guidance found");
            }
            
            /* speech guidance */
            try {
            	speechFeedback = resultJSON.getString("speech");
            	Message msg = Message.obtain();
    			msg.what = NetworkProtocol.NETWORK_RET_SPEECH;
    			msg.obj = speechFeedback;
    			this.returnMsgHandler.sendMessage(msg);
            } catch (JSONException e) {
//            	Message msg = Message.obtain();
//    			msg.what = NetworkProtocol.NETWORK_RET_DONE;
//    			this.returnMsgHandler.sendMessage(msg);
                Log.v(LOG_TAG, "no speech guidance found");
            }
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
		if (timer != null) {
		    timer.cancel();
		    timer.purge();
		}
		try {
			if(this.networkReader != null){
				this.networkReader.close();
				this.networkReader = null;
			}
		} catch (IOException e) {
		}
		try {
			if(this.networkWriter != null){
				this.networkWriter.close();
				this.networkWriter = null;
			}
		} catch (IOException e) {
		}
		try {
			if(this.tcpSocket != null){
				this.tcpSocket.shutdownInput();
				this.tcpSocket.shutdownOutput();			
				this.tcpSocket.close();
				this.tcpSocket = null;
			}
		} catch (IOException e) {
		}
	}
}
