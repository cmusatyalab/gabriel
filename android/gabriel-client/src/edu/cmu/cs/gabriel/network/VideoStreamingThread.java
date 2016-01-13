package edu.cmu.cs.gabriel.network;

import java.io.ByteArrayOutputStream;
import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileNotFoundException;
import java.io.FilenameFilter;
import java.io.IOException;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Arrays;

import org.json.JSONException;
import org.json.JSONObject;

import edu.cmu.cs.gabriel.Const;
import edu.cmu.cs.gabriel.token.TokenController;
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

public class VideoStreamingThread extends Thread {

    private static final String LOG_TAG = "VideoStreaming";
    protected File[] imageFiles = null;
    protected File[] imageFilesCompressPaths = null;
    protected Bitmap[] imageFilesCompress = new Bitmap[30];
    protected int indexImageFile = 0;
    protected int indexImageFileCompress = 0;
    protected int imageFileCompressLength = -1;

    private boolean is_running = false;
    private InetAddress remoteIP;
    private int remotePort;

    // UDP
    private DatagramSocket udpSocket = null;
    // TCP
    private Socket tcpSocket = null;
    private DataOutputStream networkWriter = null;
    private DataInputStream networkReader = null;
    private VideoControlThread networkReceiver = null;

    private byte[] frameBuffer = null;
    private long frameGeneratedTime = 0;
    private long frameCompressedTime = 0;
    private Object frameLock = new Object();
    private Handler networkHander = null;
    private long frameID = 1;   // must start from 1
    private boolean is_ping = true;

    private TokenController tokenController;

    public VideoStreamingThread(String IPString, int port, Handler handler, TokenController tokenController) {
        is_running = false;
        this.networkHander = handler;
        this.tokenController = tokenController;

        try {
            remoteIP = InetAddress.getByName(IPString);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        remotePort = port;

        // check input data at image directory
        imageFiles = this.getImageFiles(Const.TEST_IMAGE_DIR);
        //Log.e(LOG_TAG, "Number of image files in the input folder: " + imageFiles.length);
        imageFilesCompressPaths = this.getImageFiles(Const.COMPRESS_IMAGE_DIR);
        int i = 0;
        for (File path : imageFilesCompressPaths) {
//          BitmapFactory.Options options = new BitmapFactory.Options();
//            options.inPreferredConfig = Bitmap.Config.ARGB_8888;
            Bitmap bitmap = BitmapFactory.decodeFile(path.getPath());
            imageFilesCompress[i] = bitmap;
            long t_start_compressing = System.currentTimeMillis();
            Log.e(LOG_TAG, "Start compressing: " + path.getPath() + t_start_compressing);
            ByteArrayOutputStream buffer_nouse = new ByteArrayOutputStream();
            bitmap.compress(Bitmap.CompressFormat.JPEG, 67, buffer_nouse);
            Log.e(LOG_TAG, "Compressing time: " + (System.currentTimeMillis() - t_start_compressing));
            Log.e(LOG_TAG, "Compressed size: " + buffer_nouse.size());

            i++;
            if (i == 1) break;
        }
        imageFileCompressLength = i;
    }

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

    private String receiveMsg(DataInputStream reader) throws IOException {
        Log.w(LOG_TAG, "++recvMessage");
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
        Log.w(LOG_TAG, "--recvMessage: " + receivedString);
        return receivedString;
    }

    public void run() {
        this.is_running = true;
        Log.i(LOG_TAG, "Streaming thread running");

        int packet_count = 0;
        long packet_firstUpdateTime = 0;
        long packet_currentUpdateTime = 0;
        int packet_totalsize = 0;
        long packet_prevUpdateTime = 0;

        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5 * 1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
            networkReader = new DataInputStream(tcpSocket.getInputStream());
//          networkReceiver = new VideoControlThread(networkReader, this.networkHander, this.tokenController);
//          networkReceiver.start();
        } catch (IOException e) {
            Log.e(LOG_TAG, Log.getStackTraceString(e));
            Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
            this.notifyError(e.getMessage());
            this.is_running = false;
            return;
        }

        while (this.is_running) {
            try {
                // check token
                if (this.tokenController.getCurrentToken() <= 0) {
                    Log.d(LOG_TAG, "waiting");
                    continue;
                }

                if (is_ping == true) {
                    is_ping = false;
                    int i;
                    long min_diff = 1000000;
                    long best_sent_time = 0, best_server_time = 0, best_recv_time = 0;
                    for (i = 0; i < 20; i++) {
                        // send current time to server
                        ByteArrayOutputStream baos = new ByteArrayOutputStream();
                        DataOutputStream dos=new DataOutputStream(baos);
                        long sent_time = System.currentTimeMillis();
                        byte[] header = ("{\"sync_time\":" + sent_time + "}").getBytes();
                        dos.writeInt(header.length);
                        dos.write(header);
                        networkWriter.write(baos.toByteArray());
                        networkWriter.flush();

                        // receive current time at server
                        String recvMsg = this.receiveMsg(networkReader);
                        long server_time = -1;
                        try{
                            JSONObject obj = new JSONObject(recvMsg);
                            server_time = obj.getLong("sync_time");
                        } catch(JSONException e){
                            Log.e(LOG_TAG, "Sync time with server error!!");
                        }
                        long recv_time = System.currentTimeMillis();
                        if (recv_time - sent_time < min_diff) {
                            min_diff = recv_time - sent_time;
                            best_sent_time = sent_time;
                            best_server_time = server_time;
                            best_recv_time = recv_time;
                        }
                    }

                    // send message to token controller, actually for logging...
                    Message msg = Message.obtain();
                    msg.what = NetworkProtocol.NETWORK_RET_SYNC;
                    String sync_str = "" + best_sent_time + "\t" + best_server_time + "\t" + best_recv_time + "\n";
                    msg.obj = sync_str;
                    tokenController.tokenHandler.sendMessage(msg);

                    continue;
                }

                // get data
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

                    int indexCompress = indexImageFileCompress % imageFileCompressLength;
                    long t_start_compressing = System.currentTimeMillis();
                    Log.i(LOG_TAG, "Start compressing: " + indexCompress + " " + t_start_compressing);
                    ByteArrayOutputStream buffer_nouse = new ByteArrayOutputStream();
                    //imageFilesCompress[indexCompress].compress(Bitmap.CompressFormat.JPEG, 67, buffer_nouse);
                    Log.i(LOG_TAG, "End compressing: " + System.currentTimeMillis());
                    Log.e(LOG_TAG, "Compressing time: " + (System.currentTimeMillis() - t_start_compressing));

                    compressedTime = System.currentTimeMillis();
//                  compressedTime = this.frameCompressedTime;
                    sendingFrameID = this.frameID;
                    Log.v(LOG_TAG, "sending:" + sendingFrameID);
                    this.frameBuffer = null;
                    indexImageFileCompress++;
                }

                // make it as a single packet
                ByteArrayOutputStream baos = new ByteArrayOutputStream();
                DataOutputStream dos=new DataOutputStream(baos);
                byte[] header = ("{\"id\":" + sendingFrameID + "}").getBytes();
                dos.writeInt(header.length);
                dos.write(header);
                dos.writeInt(data.length);
                dos.write(data);

                this.tokenController.sendData(sendingFrameID, dataTime, compressedTime, dos.size());
                networkWriter.write(baos.toByteArray());
                networkWriter.flush();
                this.tokenController.decreaseToken();

                // measurement
                if (packet_firstUpdateTime == 0) {
                    packet_firstUpdateTime = System.currentTimeMillis();
                }
                packet_currentUpdateTime = System.currentTimeMillis();
                packet_count++;
                packet_totalsize += data.length;
                if (packet_count % 10 == 0) {
                    Log.d(LOG_TAG, "(NET)\t" + "BW: " + 8.0*packet_totalsize / (packet_currentUpdateTime-packet_firstUpdateTime)/1000 +
                            " Mbps\tCurrent FPS: " + 8.0*data.length/(packet_currentUpdateTime - packet_prevUpdateTime)/1000 + " Mbps\t" +
                            "FPS: " + 1000.0*packet_count/(packet_currentUpdateTime-packet_firstUpdateTime));
                }
                packet_prevUpdateTime = packet_currentUpdateTime;
            } catch (IOException e) {
                Log.e(LOG_TAG, e.getMessage());
                this.notifyError(e.getMessage());
                this.is_running = false;
                return;
            }

            try{
                Thread.sleep(1);
            } catch (InterruptedException e) {}
        }
        this.is_running = false;
    }

    public boolean stopStreaming() {
        is_running = false;
        if (udpSocket != null) {
            udpSocket.close();
            udpSocket = null;
        }
        if (tcpSocket != null) {
            try {
                tcpSocket.close();
            } catch (IOException e) {
            }
        }
        if (networkWriter != null) {
            try {
                networkWriter.close();
            } catch (IOException e) {
            }
        }
        if (networkReceiver != null) {
            networkReceiver.close();
        }

        return true;
    }


    private Size cameraImageSize = null;
    private long frame_count = 0, frame_firstUpdateTime = 0;
    private long frame_prevUpdateTime = 0, frame_currentUpdateTime = 0;
    private long frame_totalsize = 0;

    public void push(byte[] frame, Parameters parameters) {
        Log.v(LOG_TAG, "push");
        if (frame_firstUpdateTime == 0) {
            frame_firstUpdateTime = System.currentTimeMillis();
        }
        frame_currentUpdateTime = System.currentTimeMillis();

        int datasize = 0;
        cameraImageSize = parameters.getPreviewSize();
        if (this.imageFiles == null){
            synchronized (frameLock) {
                this.frameGeneratedTime = System.currentTimeMillis();
                YuvImage image = new YuvImage(frame, parameters.getPreviewFormat(), cameraImageSize.width,
                        cameraImageSize.height, null);
                ByteArrayOutputStream tmpBuffer = new ByteArrayOutputStream();
                image.compressToJpeg(new Rect(0, 0, image.getWidth(), image.getHeight()), 67, tmpBuffer);
                this.frameBuffer = tmpBuffer.toByteArray();
                Log.v(LOG_TAG, "compress to JPEG done");
                datasize = tmpBuffer.size();
                this.frameID++;
                frameLock.notify();
            }
        }else{
            try {
                long data_time = System.currentTimeMillis();

                // compress image
                Log.w(LOG_TAG, "Start compressing: " + System.currentTimeMillis());


                int index = indexImageFile % this.imageFiles.length;
                datasize = (int) this.imageFiles[index].length();
                FileInputStream fi = new FileInputStream(this.imageFiles[index]);
                byte[] buffer = new byte[datasize];
                fi.read(buffer, 0, datasize);
                synchronized (frameLock) {
                    this.frameBuffer = buffer;
                    this.frameGeneratedTime = data_time;
//                  this.frameCompressedTime = compressed_time;
                    this.frameID++;
                    frameLock.notify();
                }
                indexImageFile++;
//              indexImageFileCompress++;
            } catch (FileNotFoundException e) {
            } catch (IOException e) {
            }
        }

        frame_count++;
        frame_totalsize += datasize;
        if (frame_count % 50 == 0) {
            Log.e(LOG_TAG, "(IMG)\t" +
                    "BW: " + 8.0*frame_totalsize / (frame_currentUpdateTime-frame_firstUpdateTime)/1000 +
                    " Mbps\tCurrent FPS: " + 8.0*datasize/(frame_currentUpdateTime - frame_prevUpdateTime)/1000 + " Mbps\t" +
                    "FPS: " + 1000.0*frame_count/(frame_currentUpdateTime-frame_firstUpdateTime));
        }
        frame_prevUpdateTime = frame_currentUpdateTime;
    }

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
