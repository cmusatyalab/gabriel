package edu.cmu.cs.gabrielclient.network;

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

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FileOutputStream;
import java.io.FilenameFilter;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Arrays;

import edu.cmu.cs.gabrielclient.Const;
import edu.cmu.cs.gabrielclient.stream.StreamIF;
import edu.cmu.cs.gabrielclient.token.TokenController;

public class VideoStreamingThread extends Thread {

    private static final String LOG_TAG = "VideoStreaming";

    private boolean isRunning = false;

    // image files for experiments (test and compression)
    private File[] imageFiles = null;
    private File[] imageFilesCompress = null;
    private Bitmap[] imageBitmapsCompress = new Bitmap[30];
    private int indexImageFile = 0;
    private int indexImageFileCompress = 0;
    private int imageFileCompressLength = -1;


    // TCP connection
    private InetAddress remoteIP;
    private String serverAddress;
    private int remotePort;
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
//    private DataInputStream networkReader = null;

    // frame data shared between threads
    private long frameID = 0;
    private long lastSentFrameID = 0;
    private byte[] frameBuffer = null;
    private Object frameLock = new Object();

    private Handler networkHandler = null;
    private TokenController tokenController = null;
    private LogicalTime logicalTime = null;

    public VideoStreamingThread(StreamIF.StreamConfig config){
        this(config.serverIP, config.serverPort, config.callerHandler, config.tc, config.lt);
    }

    public VideoStreamingThread(String serverIP, int port, Handler handler, TokenController tokenController, LogicalTime logicalTime) {
        isRunning = false;
        this.networkHandler = handler;
        this.tokenController = tokenController;
        this.logicalTime = logicalTime;
        serverAddress = serverIP;
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
                return filename.toLowerCase().endsWith("bmp");
            }
        });
        Arrays.sort(files);
        return files;
    }

    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Video streaming thread running");
        try {
            remoteIP = InetAddress.getByName(serverAddress);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        // initialization of the TCP connection
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
//            networkReader = new DataInputStream(tcpSocket.getInputStream());
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
                    Log.w(LOG_TAG, "no token available: " + this.tokenController.getCurrentToken());
                    continue;
                }

                Log.d(LOG_TAG, "token size: " + this.tokenController.getCurrentToken());
                /*
                 * Stream data to the server.
                 */
                // get data in the frame buffer
                byte[] data = null;
                long dataTime = 0;
                long compressedTime = 0;
                synchronized(frameLock){
                    while (this.frameBuffer == null){
                        try {
                            frameLock.wait();
                        } catch (InterruptedException e) {}
                    }
                    data = this.frameBuffer;
                    dataTime = System.currentTimeMillis();

                    // TODO(junjuew) The reason here is measuring compression time of a few images
                    // is because compression happens before token control discard. This seems to be
                    // here in the first gabriel 2014 version. Need to update.
                    if (Const.IS_EXPERIMENT) { // compress pre-loaded file in experiment mode
                        long tStartCompressing = System.currentTimeMillis();
                        ByteArrayOutputStream bufferNoUse = new ByteArrayOutputStream();
                        imageBitmapsCompress[indexImageFileCompress].compress(Bitmap.CompressFormat.JPEG, 67, bufferNoUse);
                        Log.v(LOG_TAG, "Compressing time: " + (System.currentTimeMillis() - tStartCompressing));
                        indexImageFileCompress = (indexImageFileCompress + 1) % imageFileCompressLength;
                        compressedTime = System.currentTimeMillis();
                    }

                    lastSentFrameID = this.frameID;
                    Log.v(LOG_TAG, "sending:" + lastSentFrameID);
                    this.frameBuffer = null;
                }

                // make it as a single packet
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                DataOutputStream dos = new DataOutputStream(baos);
                byte[] header = ("{\"" + NetworkProtocol.HEADER_MESSAGE_FRAME_ID + "\":" + lastSentFrameID +
                        "}").getBytes();
                dos.writeInt(header.length);
                dos.write(header);
                dos.writeInt(data.length);
                dos.write(data);

                // send packet and consume tokens
                this.tokenController.logSentPacket(lastSentFrameID, dataTime, compressedTime);
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

    private void writeByteArray(ByteArrayOutputStream tmpBuffer, File toFile) throws FileNotFoundException{
        FileOutputStream fos = new FileOutputStream(toFile);
        try {
            fos.write(tmpBuffer.toByteArray());
            fos.close();
        } catch (IOException e){
            Log.v(LOG_TAG, "Cannot save byte array. IO exception");
            Log.v(LOG_TAG, e.toString());
        }
    }

    /**
     * Called whenever a new frame is generated
     * Puts the new frame into the @frameBuffer
     */
    public void push(byte[] frame, Parameters parameters) {
        Log.v(LOG_TAG, "push");
        
        if (!Const.LOAD_IMAGES) { // use real-time captured images
            synchronized (frameLock) {
                Size cameraImageSize = parameters.getPreviewSize();
                YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
                        cameraImageSize.height, null);
                ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
                // chooses quality 67 and it roughly matches quality 5 in avconv
                image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 67, tmpBuffer);
                this.frameBuffer = tmpBuffer.toByteArray();
                this.frameID++;
                if (Const.SAVE_FRAME_SEQUENCE) {
                    try {
                        File outputJpegFile =
                                new File(Const.SAVE_FRAME_SEQUENCE_DIR,
                                        String.format("%010d", this.frameID) + ".jpg");
                        writeByteArray(tmpBuffer, outputJpegFile);
                        Log.v(LOG_TAG, "save image to file" + outputJpegFile.getAbsolutePath());
                    } catch (FileNotFoundException e) {
                        Log.v(LOG_TAG, "Unable to save frame sequence. File path not found");
                    }
                }
                frameLock.notify();
            }
        } else if (Const.BYPASS_TOKEN) {
            synchronized (frameLock) {
                if (this.frameID < this.imageFiles.length) {
                    // only advance to the next frame if current frame is sent
                    if (this.frameID == lastSentFrameID) {
                        try {
                            indexImageFile = ((int) this.frameID);
                            int dataSize = (int) this.imageFiles[indexImageFile].length();
                            FileInputStream fi = new FileInputStream(this.imageFiles[indexImageFile]);
                            byte[] buffer = new byte[dataSize];
                            fi.read(buffer, 0, dataSize);
                            this.frameBuffer = buffer;
                            this.frameID += 1;
                            frameLock.notify();
                        } catch (FileNotFoundException e) {
                            Log.e(LOG_TAG, e.toString());
                        } catch (IOException e) {
                            Log.e(LOG_TAG, e.toString());
                        }
                    }
                }
            }
        } else { // use pre-captured images
            try {
                long frameIDIncValue = 1;
                if (!Const.SYNC_BASE.equals("none")) {
                    if (this.frameID > this.logicalTime.imageTime.longValue()) {
                        Log.v(LOG_TAG, "image feeding too fast; needs to wait");
                        return;
                    }
                    if (this.frameID < this.logicalTime.imageTime.longValue() - 1) {
                        Log.v(LOG_TAG, "Has to skip " + frameIDIncValue + " images to keep up with audio speed");
                        frameIDIncValue = this.logicalTime.imageTime.longValue() - 1 - this.frameID;
                    }
                }
                if (Const.SYNC_BASE.equals("video")) {
                    this.logicalTime.increaseImageTime(1);
                }

                synchronized (frameLock) {
                    this.frameID += frameIDIncValue - 1;
                }
                indexImageFile = ((int) this.frameID) % this.imageFiles.length;
                long dataTime = System.currentTimeMillis();
                int dataSize = (int) this.imageFiles[indexImageFile].length();
                FileInputStream fi = new FileInputStream(this.imageFiles[indexImageFile]);
                byte[] buffer = new byte[dataSize];
                fi.read(buffer, 0, dataSize);
                synchronized (frameLock) {
                    this.frameBuffer = buffer;
                    this.frameID += 1;
                    frameLock.notify();
                }

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
    }

    /**
     * Notifies error to the main thread
     */
    private void notifyError(String message) {
        // callback
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_CONNECT_FAILED;
        Bundle data = new Bundle();
        data.putString("message", message);
        msg.setData(data);
        this.networkHandler.sendMessage(msg);
    }

}
