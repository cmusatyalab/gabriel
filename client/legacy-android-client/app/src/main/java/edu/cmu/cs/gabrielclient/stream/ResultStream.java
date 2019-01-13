package edu.cmu.cs.gabrielclient.stream;

import android.util.Log;

import org.json.JSONException;
import org.json.JSONObject;

import edu.cmu.cs.gabrielclient.GabrielClientActivity;
import edu.cmu.cs.gabrielclient.network.ConnectionConfig;
import edu.cmu.cs.gabrielclient.network.NetworkProtocol;
import edu.cmu.cs.gabrielclient.network.RateLimitReceivingThread;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;

public class ResultStream implements LifeCycleIF {
    private static final String LOG_TAG = GabrielClientActivity.class.getSimpleName();
    ConnectionConfig config;
    private RateLimitReceivingThread networkThread;

    public ResultStream(ConnectionConfig config) {
        this.config = config;
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


    @Override
    public void onResume() {
        networkThread = new RateLimitReceivingThread(this.config);
        networkThread.start();
    }

    @Override
    public void onPause() {
        if ((networkThread != null) && (networkThread.isAlive())) {
            networkThread.close();
            networkThread = null;
        }
    }

    @Override
    public void onDestroy() {

    }
}

