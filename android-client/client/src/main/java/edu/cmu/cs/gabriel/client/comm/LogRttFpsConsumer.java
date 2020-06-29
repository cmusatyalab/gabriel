package edu.cmu.cs.gabriel.client.comm;

import android.util.Log;

import edu.cmu.cs.gabriel.client.function.Consumer;

public class LogRttFpsConsumer implements Consumer<RttFps> {
    private static final String TAG = "LogRttFpsConsumer";

    @Override
    public void accept(RttFps rttFps) {
        Log.i(TAG, "Interval FPS: " + rttFps.getFps());
        Log.i(TAG, "Interval RTT: " + rttFps.getRtt() + " ms");
    }
}
