package edu.cmu.cs.gabriel.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FilenameFilter;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Arrays;

import org.json.JSONException;
import org.json.JSONObject;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;
import android.graphics.YuvImage;
import android.hardware.Camera.Parameters;
import android.hardware.Camera.Size;
import android.os.Bundle;
import android.os.Handler;
import android.os.Message;
import android.util.Log;
import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.token.TokenController;

public class VideoStreamingThread extends Thread {

    private static final String LOG_TAG = "VideoStreaming";

    private boolean isRunning = false;
    private boolean isPing = true;
    
    // image files for experiments (test and compression)
    private File[] imageFiles = null;
    private File[] imageFilesCompress = null;
    private Bitmap[] imageBitmapsCompress = new Bitmap[30];
    private int indexImageFile = 0;
    private int indexImageFileCompress = 0;
    private int imageFileCompressLength = -1;


    // TCP connection
    private InetAddress remoteIP;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
    private DataInputStream networkReader = null;
    private VideoControlThread networkReceiver = null;

    // frame data shared between threads
    private long frameID = 0;
    private byte[] frameBuffer = null;
    private Object frameLock = new Object();

    private Handler networkHander = null;
    private TokenController tokenController = null;

    public VideoStreamingThread(String serverIP, int port, Handler handler, TokenController tokenController) {
        isRunning = false;
        this.networkHander = handler;
        this.tokenController = tokenController;

        try {
            remoteIP = InetAddress.getByName(serverIP);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        remotePort = port;

        if (Const.LOAD_IMAGES) {
            // check input data at image directory
            imageFiles = this.getImageFiles(Const.TEST_IMAGE_DIR);
            if (imageFiles.length == 0) {
                // TODO: notify error to the main thread
                Log.e(LOG_TAG, "test image directory empty!");
            } else {
                Log.i(LOG_TAG, "Number of image files in the input folder: " + imageFiles.length);
            }
        }

        if (Const.IS_EXPERIMENT) {
            imageFilesCompress = this.getImageFiles(Const.COMPRESS_IMAGE_DIR);
            int i = 0;
            for (File path : imageFilesCompress) {
    //          BitmapFactory.Options options = new BitmapFactory.Options();
    //            options.inPreferredConfig = Bitmap.Config.ARGB_8888;
                Bitmap bitmap = BitmapFactory.decodeFile(path.getPath());
                imageBitmapsCompress[i] = bitmap;
                i++;
                if (i == Const.MAX_COMPRESS_IMAGE) break;
            }
            imageFileCompressLength = i;
        }
    }

    /**
     * @return all files within @imageDir
     */
    private File[] getImageFiles(File imageDir) {
        if (imageDir == null){
            return null;
        }
        File[] files = imageDir.listFiles(new FilenameFilter() {
            @Override
            public boolean accept(File dir, String filename) {
                if (filename.toLowerCase().endsWith("jpg"))
                    return true;
                if (filename.toLowerCase().endsWith("jpeg"))
                    return true;
                if (filename.toLowerCase().endsWith("png"))
                    return true;
                if (filename.toLowerCase().endsWith("bmp"))
                    return true;
                return false;
            }
        });
        Arrays.sort(files);
        return files;
    }

    /**
     * @return a String representing the received message from @reader
     */
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

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Streaming thread running");

        // initialization of the TCP connection
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
            networkReader = new DataInputStream(tcpSocket.getInputStream());
//          networkReceiver = new VideoControlThread(networkReader, this.networkHander, this.tokenController);
//          networkReceiver.start();
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing network socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
            return;
        }

        while (this.isRunning) {
            try {
                // check token
                if (this.tokenController.getCurrentToken() <= 0) {
                    // this shouldn't happen since getCurrentToken will block until there is token
                    Log.w(LOG_TAG, "no token available"); 
                    continue;
                }

                /*
                 * The first part is pinging to synchronize time with the server every time the applications starts
                 */
                if (Const.IS_EXPERIMENT) {
                    if (isPing) {
                        isPing = false;
                        long min_diff = 1000000;
                        long bestSentTime = 0, bestServerTime = 0, bestRecvTime = 0;
                        for (int i = 0; i < Const.MAX_PING_TIMES; i++) {
                            // send current time to server
                            ByteArrayOutputStream baos = new ByteArrayOutputStream();
                            DataOutputStream dos = new DataOutputStream(baos);
                            long sentTime = System.currentTimeMillis();
                            byte[] jsonData = ("{\"sync_time\":" + sentTime + "}").getBytes();
                            dos.writeInt(jsonData.length);
                            dos.write(jsonData);
                            networkWriter.write(baos.toByteArray());
                            networkWriter.flush();
    
                            // receive current time at server
                            String recvMsg = this.receiveMsg(networkReader);
                            long serverTime = -1;
                            try{
                                JSONObject obj = new JSONObject(recvMsg);
                                serverTime = obj.getLong("sync_time");
                            } catch(JSONException e){
                                Log.e(LOG_TAG, "Sync time with server error!!");
                            }
                            
                            long recvTime = System.currentTimeMillis();
                            if (recvTime - sentTime < min_diff) {
                                min_diff = recvTime - sentTime;
                                bestSentTime = sentTime;
                                bestServerTime = serverTime;
                                bestRecvTime = recvTime;
                            }
                        }

                        // send message to token controller, actually for logging...
                        Message msg = Message.obtain();
                        msg.what = NetworkProtocol.NETWORK_RET_SYNC;
                        String sync_str = "" + bestSentTime + "\t" + bestServerTime + "\t" + bestRecvTime + "\n";
                        msg.obj = sync_str;
                        tokenController.tokenHandler.sendMessage(msg);
    
                        continue;
                    }
                }

                /*
                 * The second part is to stream real data to the server.
                 */
                // get data in the frame buffer
                byte[] data = null;
                long dataTime = 0;
                long compressedTime = 0;
                long sendingFrameID = 0;
                synchronized(frameLock){
                    while (this.frameBuffer == null){
                        try {
                            frameLock.wait();
                        } catch (InterruptedException e) {}
                    }
                    data = this.frameBuffer;
                    dataTime = System.currentTimeMillis();

                    if (Const.IS_EXPERIMENT) { // compress pre-loaded file in experiment mode
                        long tStartCompressing = System.currentTimeMillis();
                        ByteArrayOutputStream bufferNoUse = new ByteArrayOutputStream();
                        imageBitmapsCompress[indexImageFileCompress].compress(Bitmap.CompressFormat.JPEG, 67, bufferNoUse);
                        Log.v(LOG_TAG, "Compressing time: " + (System.currentTimeMillis() - tStartCompressing));
                        indexImageFileCompress = (indexImageFileCompress + 1) % imageFileCompressLength;
                        compressedTime = System.currentTimeMillis();
                    }

                    sendingFrameID = this.frameID;
                    Log.v(LOG_TAG, "sending:" + sendingFrameID);
                    this.frameBuffer = null;
                }

                // make it as a single packet
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                DataOutputStream dos = new DataOutputStream(baos);
                byte[] header = ("{\"" + NetworkProtocol.HEADER_MESSAGE_FRAME_ID + "\":" + sendingFrameID + "}").getBytes();
                dos.writeInt(header.length);
                dos.write(header);
                dos.writeInt(data.length);
                dos.write(data);

                // send packet and consume tokens
                this.tokenController.logSentPacket(sendingFrameID, dataTime, compressedTime);
                this.tokenController.decreaseToken();
                networkWriter.write(baos.toByteArray());
                networkWriter.flush();
                
            } catch (IOException e) {
                Log.e(LOG_TAG, "Error in sending packet: " + e);
                this.notifyError(e.getMessage());
                this.isRunning = false;
                return;
            }
        }
        this.isRunning = false;
    }

    /**
     * Called whenever a new frame is generated
     * Puts the new frame into the @frameBuffer
     */
    public void push(byte[] frame, Parameters parameters) {
        Log.v(LOG_TAG, "push");
        
        if (!Const.LOAD_IMAGES){ // use real-time captured images
            synchronized (frameLock) {
                Size cameraImageSize = parameters.getPreviewSize();
                YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
                        cameraImageSize.height, null);
                ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
                // chooses quality 67 and it roughly matches quality 5 in avconv
                image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 67, tmpBuffer);
                this.frameBuffer = tmpBuffer.toByteArray();
                this.frameID++;
                frameLock.notify();
            }
        } else { // use pre-captured images
            try {
                long dataTime = System.currentTimeMillis();
                int dataSize = (int) this.imageFiles[indexImageFile].length();
                FileInputStream fi = new FileInputStream(this.imageFiles[indexImageFile]);
                byte[] buffer = new byte[dataSize];
                fi.read(buffer, 0, dataSize);
                synchronized (frameLock) {
                    this.frameBuffer = buffer;
                    this.frameID++;
                    frameLock.notify();
                }
                indexImageFile = (indexImageFile + 1) % this.imageFiles.length;
            } catch (FileNotFoundException e) {
            } catch (IOException e) {
            }
        }
    }

    public void stopStreaming() {
        isRunning = false;
        if (tcpSocket != null) {
            try {
                tcpSocket.close();
            } catch (IOException e) {}
        }
        if (networkWriter != null) {
            try {
                networkWriter.close();
            } catch (IOException e) {}
        }
        if (networkReceiver != null) {
            networkReceiver.close();
        }
    }

    /**
     * Notifies error to the main thread
     */
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
