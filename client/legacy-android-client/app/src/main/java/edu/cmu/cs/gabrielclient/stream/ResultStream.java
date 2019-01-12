package edu.cmu.cs.gabrielclient.stream;

import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import edu.cmu.cs.gabrielclient.GabrielClientActivity;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.network.RateLimitReceivingThread;

public class ResultStream implements StreamIF {

    private static final String LOG_TAG = GabrielClientActivity.class.getSimpleName();

    private RateLimitReceivingThread rateLimitReceivingThread;

    public ResultStream(StreamConfig config) {
        init(config);
    }

    @Override
    public void init(StreamConfig config) {
        rateLimitReceivingThread = new RateLimitReceivingThread(config);
    }

    @Override
    public void start() {
        rateLimitReceivingThread.start();
    }

    @Override
    public void stop() {
        if ((rateLimitReceivingThread != null) && (rateLimitReceivingThread.isAlive())) {
            rateLimitReceivingThread.close();
            rateLimitReceivingThread = null;
        }
    }

    public String parseReturnMsg(String recvData) {
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
        } catch (JSONException e) {
            Log.e(LOG_TAG, "Invalid result message. Parsing failed.");
            Log.e(LOG_TAG, recvData);
            return null;
        }
        Log.v(LOG_TAG, "frame (" + frameID + ") result received.");

        if (!status.equals("success")) {
            Log.w(LOG_TAG, "Server returns failure in processing.");
            Log.w(LOG_TAG, recvData);
            return null;
        }

        return result;
    }


}

