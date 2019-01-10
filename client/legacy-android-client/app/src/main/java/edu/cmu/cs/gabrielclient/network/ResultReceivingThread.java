
package edu.cmu.cs.gabrielclient.network;

import java.io.DataInputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.UnknownHostException;
import java.util.Timer;
import java.util.TimerTask;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.os.Handler;
import android.os.Message;
import android.util.Base64;
import android.util.Log;
import edu.cmu.cs.gabrielclient.token.ReceivedPacketInfo;

public class ResultReceivingThread extends Thread {

    private static final String LOG_TAG = "ResultThread";

    private boolean isRunning = false;

    // TCP connection
    private InetAddress remoteIP;
    private String serverAddress;
    private int remotePort;
    private Socket tcpSocket;
    private DataOutputStream networkWriter;
    private DataInputStream networkReader;

    private Handler returnMsgHandler;

    // animation
    private Timer timer = null;
    private Bitmap[] animationFrames = new Bitmap[10];
    private int[] animationPeriods = new int[10]; // how long each frame is shown, in millisecond
    private int animationDisplayIdx = -1;
    private int nAnimationFrames = -1;


    public ResultReceivingThread(String serverIP, int port, Handler returnMsgHandler) {
        isRunning = false;
        this.returnMsgHandler = returnMsgHandler;
        serverAddress = serverIP;
        remotePort = port;
    }

    @Override
    public void run() {
        this.isRunning = true;
        Log.i(LOG_TAG, "Result receiving thread running");
        try {
            remoteIP = InetAddress.getByName(serverAddress);
        } catch (UnknownHostException e) {
            Log.e(LOG_TAG, "unknown host: " + e.getMessage());
        }
        try {
            tcpSocket = new Socket();
            tcpSocket.setTcpNoDelay(true);
            tcpSocket.connect(new InetSocketAddress(remoteIP, remotePort), 5*1000);
            networkWriter = new DataOutputStream(tcpSocket.getOutputStream());
            networkReader = new DataInputStream(tcpSocket.getInputStream());
        } catch (IOException e) {
            Log.e(LOG_TAG, "Error in initializing Data socket: " + e);
            this.notifyError(e.getMessage());
            this.isRunning = false;
            return;
        }

        while (isRunning == true){
            try {
                String recvMsg = this.receiveMsg(networkReader);
                this.notifyReceivedData(recvMsg);

            } catch (IOException e) {
                Log.w(LOG_TAG, "Error in receiving result, maybe because the app has paused");
                this.notifyError(e.getMessage());
                break;
            }
        }
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


    private void notifyReceivedData(String recvData) {
        // convert the message to JSON
        String status = null;
        String result = null;
        String sensorType = null;
        long frameID = -1;
        String engineID = "";
        int injectedToken = 0;

        try {
            JSONObject recvJSON = new JSONObject(recvData);
            status = recvJSON.getString("status");
            result = recvJSON.getString(NetworkProtocol.HEADER_MESSAGE_RESULT);
            sensorType = recvJSON.getString(NetworkProtocol.SENSOR_TYPE_KEY);
            frameID = recvJSON.getLong(NetworkProtocol.HEADER_MESSAGE_FRAME_ID);
            engineID = recvJSON.getString(NetworkProtocol.HEADER_MESSAGE_ENGINE_ID);
            //injectedToken = recvJSON.getInt(NetworkProtocol.HEADER_MESSAGE_INJECT_TOKEN);
        } catch (JSONException e) {
            Log.e(LOG_TAG, recvData);
            Log.e(LOG_TAG, "the return message has no status field");
            return;
        }

        Log.v(LOG_TAG, "result received: " + frameID);

        // return status
        if (sensorType.equals(NetworkProtocol.SENSOR_JPEG)) {
            Message msg = Message.obtain();
            msg.what = NetworkProtocol.NETWORK_RET_MESSAGE;
            msg.obj = new ReceivedPacketInfo(frameID, engineID, status);
            this.returnMsgHandler.sendMessage(msg);
        }

        if (!status.equals("success")) {
            if (sensorType.equals(NetworkProtocol.SENSOR_JPEG)) {
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_DONE;
                msg.obj = new ReceivedPacketInfo(frameID, engineID, status);
                this.returnMsgHandler.sendMessage(msg);
            }
            return;
        }

        // TODO: refilling tokens
//        if (injectedToken > 0){
//            this.tokenController.increaseTokens(injectedToken);
//        }

        if (result != null){
            /* parsing result */
            JSONObject resultJSON = null;
            try {
                resultJSON = new JSONObject(result);
            } catch (JSONException e) {
                Log.e(LOG_TAG, "Result message not in correct JSON format");
            }

            String speechFeedback = "";
            Bitmap imageFeedback = null;


            // image guidance
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

            // video guidance
            try {
                String videoURL = resultJSON.getString("video");
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_VIDEO;
                msg.obj = videoURL;
                this.returnMsgHandler.sendMessage(msg);
            } catch (JSONException e) {
                Log.v(LOG_TAG, "no video guidance found");
            }

            // animation guidance
            try {
                JSONArray animationArray = resultJSON.getJSONArray("animation");
                nAnimationFrames = animationArray.length();
                for (int i = 0; i < nAnimationFrames; i++) {
                    JSONArray frameArray = animationArray.getJSONArray(i);
                    String animationFrameString = frameArray.getString(0);
                    byte[] data = Base64.decode(animationFrameString.getBytes(), Base64.DEFAULT);
                    animationFrames[i] = BitmapFactory.decodeByteArray(data,0,data.length);
                    animationPeriods[i] = frameArray.getInt(1);
                }
                animationDisplayIdx = -1;
                if (timer == null) {
                    timer = new Timer();
                    timer.schedule(new animationTask(), 0);
                }
            } catch (JSONException e) {
                Log.v(LOG_TAG, "no animation guidance found");
            }

            // speech guidance
            try {
                speechFeedback = resultJSON.getString("speech");
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_SPEECH;
                msg.obj = speechFeedback;
                this.returnMsgHandler.sendMessage(msg);
            } catch (JSONException e) {
                Log.v(LOG_TAG, "no speech guidance found");
            }

            // done processing return message
            if (sensorType.equals(NetworkProtocol.SENSOR_JPEG)) {
                Message msg = Message.obtain();
                msg.what = NetworkProtocol.NETWORK_RET_DONE;
                this.returnMsgHandler.sendMessage(msg);
            }
        }
    }

    private class animationTask extends TimerTask {
        @Override
        public void run() {
            Log.v(LOG_TAG, "Running timer task");
            animationDisplayIdx = (animationDisplayIdx + 1) % nAnimationFrames;
            Message msg = Message.obtain();
            msg.what = NetworkProtocol.NETWORK_RET_ANIMATION;
            msg.obj = animationFrames[animationDisplayIdx];
            returnMsgHandler.sendMessage(msg);
            if (isRunning)
                timer.schedule(new animationTask(), animationPeriods[animationDisplayIdx]);
        }
    }

    public void close() {
        this.isRunning = false;

        if (timer != null) {
            timer.cancel();
            timer = null;
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

    /**
     * Notifies error to the main thread
     */
    private void notifyError(String errorMessage) {
        Message msg = Message.obtain();
        msg.what = NetworkProtocol.NETWORK_RET_FAILED;
        msg.obj = errorMessage;
        this.returnMsgHandler.sendMessage(msg);
    }
}
