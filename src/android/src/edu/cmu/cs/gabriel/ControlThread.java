package edu.cmu.cs.gabriel;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.InetAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.StringTokenizer;

import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.util.Log;

public class ControlThread extends Thread {
    private static final String LOG_TAG = "Control Thread";
	
    // output codes
    public static final int CODE_TCP_SETUP_SUCCESS = 0;
    public static final int CODE_TCP_SETUP_FAIL = 1;
    public static final int CODE_STREAM_PORT = 2;
    
    // input codes
    public static final int CODE_QUERY_PORT = 0;
    public static final int CODE_CLOSE_CONNECTION = 1;
    
    Handler pHandler = null;
    
    // network address for control channel
    private InetAddress remoteIP;
    private int remotePort;
    
    private Socket tcpSocket;
    private PrintWriter TCPWriter;
    private BufferedReader TCPReader;
    private static String rtn = "\r\n";

    public ControlThread(Handler handler, String IPString) {
		pHandler = handler;		
        tcpSocket = null;
		
        try {	
            remoteIP = InetAddress.getByName(IPString);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "Unknown host: " + e.getMessage());
        }	
    }
    
    public Handler getHandler() {
        return mHandler;
    }

    public void run() {
        initConnection(); // initialize TCP connection
        
        Looper.prepare();
        
        Looper.loop();
    }

    private Handler mHandler = new Handler() {
        public void handleMessage(Message msg_in) {
            if (msg_in.what == CODE_QUERY_PORT) {   // Get the remote UDP port for specific stream              
                Message msg_out = Message.obtain();
                msg_out.what = CODE_STREAM_PORT;
                msg_out.arg1 = GabrielClientActivity.VIDEO_STREAM_PORT;
                pHandler.sendMessage(msg_out);
            } else if (msg_in.what == CODE_CLOSE_CONNECTION) {
                try {
                    if (TCPWriter != null)
                        TCPWriter.close();
                    if (TCPReader != null)
                        TCPReader.close();
                    if (tcpSocket != null)
                        tcpSocket.close();
                    TCPWriter = null;
                    TCPReader = null;
                    tcpSocket = null;
                } catch (IOException e) {
                    Log.e(LOG_TAG, "Error in closing TCP connection: " + e.getMessage());
                }
            }
        }
    };
    private void initConnection() {
        if (null == tcpSocket || tcpSocket.isClosed()){         
            Log.d(LOG_TAG, "Trying to connect to " + remoteIP.toString() + ":" + remotePort);
            
            Message msg = Message.obtain();
			msg.what = CODE_TCP_SETUP_SUCCESS;
			pHandler.sendMessage(msg);
			/*
			 * tcpSocket = new Socket(remoteIP, remotePort);
			 * tcpSocket.setKeepAlive(true); TCPWriter = new
			 * PrintWriter(tcpSocket.getOutputStream(), true); TCPReader = new
			 * BufferedReader(new
			 * InputStreamReader(tcpSocket.getInputStream())); Log.i(LOG_TAG,
			 * "Control channel successfully built, connected to" +
			 * tcpSocket.getInetAddress().toString() + ":" +
			 * tcpSocket.getPort()); msg.what = CODE_TCP_SETUP_SUCCESS;
			 * pHandler.sendMessage(msg);
			 */
        } else {
            Log.w(LOG_TAG, "TCP socket is already open, connected to" + 
                    tcpSocket.getInetAddress().toString() + ":" + tcpSocket.getPort());
        }
    }
}
