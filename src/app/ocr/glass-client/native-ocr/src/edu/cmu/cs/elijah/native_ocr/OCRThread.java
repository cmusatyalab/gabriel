package edu.cmu.cs.elijah.native_ocr;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.DataInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.net.DatagramSocket;
import java.net.Socket;
import java.net.SocketException;
import java.nio.ByteBuffer;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Environment;
import android.os.Handler;
import android.util.Log;

import com.googlecode.tesseract.android.TessBaseAPI;

public class OCRThread extends Thread {
    private static final String LOG_TAG = "OCR Thread";
    
    private volatile boolean running = true;
    
    static final int BUFFER_SIZE = 102400;    // need to negotiate with server
    
    private static final String TESSBASE_PATH = "/mnt/sdcard/tesseract/";
    private static final String DEFAULT_LANGUAGE = "eng";
    
    // thread communication command constants
    public static final int CODE_SEND_PACKET = 0;
    public static final int CODE_SEND_QUERY_ID = 1;
    public static final int CODE_SEND_CONTENT = 2;
    public static final int CODE_SEND_STOP = 3;
    public static final int CODE_SEND_USER_ID = 4;
    // mode constants
    public static final int MODE_NATIVE = 0;
    public static final int MODE_OFFLOAD = 1;
    // network protocol index
    public static final int NETWORK_UNUSED = 0;
    public static final int NETWORK_UDP = 1;
    public static final int NETWORK_TCP = 2;
    
    private Handler mHandler;
    private MainActivity pActivity;
    
    private int mode;
    private int protocolIndex;
    
    // network communication
    private String remoteIP = "";
    private int remotePort = -1;
    private DatagramSocket udpSocket;
    private Socket socket;
    private DataInputStream inStream;
    private OutputStream outStream;
    private PrintWriter TCPWriter;
    private BufferedReader TCPReader;
    
    // files
    private FileInputStream inputStream;
    private FileOutputStream localFileStream;
//    private File videoFile = new File(Environment.getExternalStorageDirectory() + File.separator + "streaming.mp4");
    
    // OCR
    private TessBaseAPI baseApi = null;
    	
    public OCRThread(int mode, int protocolIndex, String IPString, int port, MainActivity activity) {
        this.mode = mode;
        this.protocolIndex = protocolIndex;
        udpSocket = null;
        socket = null;
        remoteIP = IPString;
        remotePort = port;
        pActivity = activity;
    }
    	
    public void run() {
        Log.i(LOG_TAG, "OCR thread running");
        	
        try {
            switch (protocolIndex) {    // currently only support TCP
            case NETWORK_TCP:
            	if ((socket != null) && (!socket.isClosed()))
            		socket.close();
            	socket = new Socket(remoteIP, remotePort);
        		if (socket.isConnected())
        			Log.i(LOG_TAG, "Socket Connected");
        		else
        			Log.e(LOG_TAG, "Socket not connected!");
        		
        		outStream = socket.getOutputStream();
        		inStream = new DataInputStream(socket.getInputStream());
            }
        } catch (SocketException e) {
            Log.e(LOG_TAG, "Error in initializing socket: " + e.getMessage());
        } catch (IOException ex) {
    		Log.e(LOG_TAG, "Socket Problem: " + ex.toString());
    		return;
        }
        
        // initialization
        baseApi = new TessBaseAPI();
        baseApi.init(TESSBASE_PATH, DEFAULT_LANGUAGE);
        baseApi.setPageSegMode(TessBaseAPI.PageSegMode.PSM_AUTO);
        Log.i(LOG_TAG, "tesseract library ready");
        
        int imageIdx = 1;
        String imageFolderPath = Environment.getExternalStorageDirectory().toString() + "/OCR_test/";
        byte[] jpegData = null;
        long lastLatency = 0;
        try {
            while (running && imageIdx <= 300) {
                String imageFile = imageFolderPath + String.format("image-%03d.jpeg", imageIdx);
//                String imageFile = imageFolderPath + String.format("test.jpg");
                Log.v(LOG_TAG, "Now reading file from " + imageFile);
                BufferedInputStream bufInput = new BufferedInputStream(new FileInputStream(imageFile));
                int fileSize = (int) new File(imageFile).length();
                jpegData = new byte[fileSize];
                int bytesRead = 0;
                // make sure to read the whole file
                while (bytesRead < fileSize) {
                    int bytesNum = bufInput.read(jpegData, bytesRead, fileSize - bytesRead);
                    bytesRead += bytesNum;
                }
                Log.v(LOG_TAG, "Now processing image " + imageIdx);
                boolean first_image = (imageIdx == 1);
                long start_time = System.currentTimeMillis();
                String latencyFile = "";
                if (mode == MODE_NATIVE) {
                    BitmapFactory.Options bitmapFatoryOptions = new BitmapFactory.Options();
                    bitmapFatoryOptions.inPreferredConfig = Bitmap.Config.ARGB_8888;
                    Bitmap bmp = BitmapFactory.decodeByteArray(jpegData, 0, jpegData.length, bitmapFatoryOptions); 
                    
                    // OCR the current frame
                    String result = processImageOCR(bmp);                    
                    Log.v(LOG_TAG, "processed one frame");
                    latencyFile = imageFolderPath + String.format("latency_native.txt");
                    
//                    try {
//                        String resultFile = imageFolderPath + String.format("output_native.txt");
//                        OutputStreamWriter outputStreamWriter = new OutputStreamWriter(new FileOutputStream(resultFile, !first_image));
//                        outputStreamWriter.write(String.format("frame %d: %s\n", imageIdx, result));
//                        outputStreamWriter.close();
//                    } catch (IOException e) {
//                        Log.e("Exception", "File write failed: " + e.toString());
//                    } 
                } else if (mode == MODE_OFFLOAD) {
                    byte[] dataLen = pack(jpegData.length);
                    outStream.write(dataLen);
                    Log.v(LOG_TAG, "sent image length");
                    outStream.write(jpegData);
                    Log.v(LOG_TAG, "sent one image");
                    
                    int resultLen = inStream.readInt();
                    Log.v(LOG_TAG, "Result length: " + resultLen);
//                    int resultLen = unpack(receiveAll(inStream, 4));
                    byte[] result = receiveAll(inStream, resultLen);
                    Log.v(LOG_TAG, "Got some results");
                    latencyFile = imageFolderPath + String.format("latency_offload.txt");
                }
                long end_time = System.currentTimeMillis();
                try {
                    OutputStreamWriter outputStreamWriter = new OutputStreamWriter(new FileOutputStream(latencyFile, !first_image));
                    long jitter = end_time - start_time - lastLatency;
                    if (jitter < 0) jitter = -jitter;
                    outputStreamWriter.write(String.format("%d, %d, %d, %d, %d\n", 
                            imageIdx, start_time, end_time, end_time - start_time, jitter));
                    lastLatency = end_time - start_time;
                    outputStreamWriter.close();
                }
                catch (IOException e) {
                    Log.e("Exception", "File write failed: " + e.toString());
                } 
                
                imageIdx++;
            }
        } catch (FileNotFoundException e) {
            Log.i(LOG_TAG, "Finished reading all jpeg files");
        } catch (IOException e) {
            Log.e(LOG_TAG, "Unknown file read error: " + e.getMessage());
        }
        
        pActivity.stopBatteryRecording();
        
        try {
            Socket tmpSocket = null;
            tmpSocket = new Socket("typhoon.elijah.cs.cmu.edu", 9999);
            if (tmpSocket.isConnected())
                Log.i(LOG_TAG, "Temp socket Connected");
            else
                Log.e(LOG_TAG, "Temp socket not connected!");
            
        } catch (SocketException e) {
            Log.e(LOG_TAG, "Error in initializing socket: " + e.getMessage());
        } catch (IOException ex) {
            Log.e(LOG_TAG, "Socket Problem: " + ex.toString());
            return;
        }
        
        
	}
    
    // The main function of native OCR processing
    private String processImageOCR(Bitmap bmp) {
        baseApi.setImage(bmp);
        
        // real OCR recognition here
        Log.d("OCR processing", "OCR starts");
        final String outputText = baseApi.getUTF8Text();
        Log.d("OCR processing", outputText);
        
        return outputText;
    }
    
    // Receive a number of bytes from buffered input stream
    private byte[] receiveAll(DataInputStream inStream, int len) {
        byte[] data = new byte[len];
        int bytesRead = 0;
        // make sure to read all the bytes
        while (bytesRead < len) {
            try {
                int bytesNum = inStream.read(data, bytesRead, len - bytesRead);
                bytesRead += bytesNum;
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error reading results from network: " + e.getMessage());
            }
        }
        return data;
    }
        
    // Big-Endian, usigned int
    private byte[] pack(int n) {
    	if (n < 0) 
    	{
    		Log.e("TCPpacket", "Pack number negative: " + n);
    		return null;
    	}
    	ByteBuffer pump = ByteBuffer.allocate(4);
    	pump.putInt(n);
    	byte[] bytes = pump.array();
    	Log.i("TCPpacket", "Pack " + n + " to " + bytes[0] + ", " + bytes[1] + ", " + bytes[2]+ ", " + bytes[3]);
    	return bytes;
    }
    
    private int unpack(byte[] bytes)
    {
        int n = 0;
//        Log.v("LOG_TAG", )
        n = ((int) bytes[0] << 32) | ((int) bytes[1] << 16) | ((int)bytes[2] << 8) | ((int) bytes[3]);
        //n = (int) bytes[3];
//        Log.i(LOG_TAG, "UnPack " + ((int)bytes[0]<< 32) + ", " + ((int) bytes[1] << 16) + ", " + ((int)bytes[2] << 8)+ ", " + bytes[3]);
        Log.i(LOG_TAG, "UnPack " + bytes[0] + ", " + bytes[1] + ", " + bytes[2]+ ", " + bytes[3] + " to " + n);
        return n;
    }
		
    public void stopOCR() {
//        if (udpSocket != null) {
//            udpSocket.close();
//            udpSocket = null;
//        }
//        if (inputStream != null)
//            inputStream.close();
//        if (localFileStream != null)
//            localFileStream.close();
//        inputStream = null;
//        localFileStream = null;
//		
//        socket.close();
        
        running = false;
    }
}
