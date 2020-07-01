package edu.cmu.cs.gabriel.client.consumer;

import android.util.Log;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.SourceRttFps;

public class LogMeasurementConsumer implements Consumer<SourceRttFps> {
    private static final String TAG = "LogMeasurementConsumer";

    @Override
    public void accept(SourceRttFps sourceRttFps) {
        String sourceName = sourceRttFps.getSourceName();
        Log.i(TAG, sourceName + " Interval FPS: " + sourceRttFps.getFps());
        Log.i(TAG, sourceName + " Average RTT for interval: " + sourceRttFps.getRtt() + " ms");
    }
}
