package edu.cmu.cs.gabriel.client.consumer;

import android.util.Log;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.IntervalMeasurement;

public class LogMeasurementConsumer implements Consumer<IntervalMeasurement> {
    private static final String TAG = "LogMeasurementConsumer";

    @Override
    public void accept(IntervalMeasurement intervalMeasurement) {
        String sourceName = intervalMeasurement.getSourceName();
        Log.i(TAG, sourceName + " Average RTT for interval: " + intervalMeasurement.getIntervalRtt()
                + " ms");
        Log.i(TAG, sourceName + " Overall Average RTT: " + intervalMeasurement.getOverallRtt()
                + " ms");
        Log.i(TAG, sourceName + " Interval FPS: " + intervalMeasurement.getIntervalFps());
        Log.i(TAG, sourceName + " Overall FPS: " + intervalMeasurement.getOverallFps());
    }
}
