package edu.cmu.cs.gabriel;

import java.io.FileDescriptor;
import java.io.IOException;

import android.hardware.Camera;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.util.Log;
import android.view.Surface;

public class CameraConnector {
    private static final String LOG_TAG = "Camera Recording Thread";
    
    private static final int LOCAL_BUFF_SIZE = 102400;
    
    public static final int MEDIA_TYPE_IMAGE = 1;
    public static final int MEDIA_TYPE_VIDEO = 2;
        
    
    /* The media recorder cannot write data to a remote socket directly,
     *  so we have to create a local socket first, and the local receiver transmits data to a remote socket (in StreamingThread)
     *  The performance reduction induced by this method is unknown.
     */
    private LocalSocket localSender, localReceiver;
    private LocalServerSocket localLoop;
	
    public CameraConnector() {
        localSender = null;
        localReceiver = null;
        localLoop = null;
    }
    
    public FileDescriptor getOutputFileDescriptor() {
        return localReceiver.getFileDescriptor();
    }
    
    public FileDescriptor getInputFileDescriptor() {
        return localSender.getFileDescriptor();
    }
	
    public void init() {
        try {
            localLoop = new LocalServerSocket("videoserver");
            localReceiver = new LocalSocket();
            localReceiver.connect(localLoop.getLocalSocketAddress());
            localReceiver.setReceiveBufferSize(LOCAL_BUFF_SIZE);
            localReceiver.setSendBufferSize(LOCAL_BUFF_SIZE);
			
			localSender = localLoop.accept();
            localSender.setReceiveBufferSize(LOCAL_BUFF_SIZE);
            localSender.setSendBufferSize(LOCAL_BUFF_SIZE);
			
            Log.d(LOG_TAG, "Done: init()");
		}catch(IOException e) {
		    Log.e(LOG_TAG, "Error in initializing local socket: " + e);
		}
    }
    
    public void close() {
        if (localReceiver != null){
			try {
				localReceiver.close();
			} catch (IOException e) {}
        }
        if (localSender != null){
			try {
				localSender.close();
			} catch (IOException e) {}
        }
        if (localLoop != null){
			try {
				localLoop.close();
			} catch (IOException e) {}
		}
        
    }
}
