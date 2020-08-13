package edu.cmu.cs.gabriel.client.observer;

import android.os.SystemClock;
import android.util.Log;
import android.util.LongSparseArray;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

public class MeasurementSource {
    private static final String TAG = "MeasurementSource";

    private final String sourceName;
    private final int outputFreq;
    private final Consumer<IntervalMeasurement> intervalReporter;
    private final Map<Long, Long> sentTimestamps;
    private final LongSparseArray<Long> receivedTimestamps;
    private long count;
    private long startTime;
    private long intervalStartTime;
    private long lastReceiveTime;
    private long overallTotalRtt;

    public MeasurementSource(
            String sourceName, int outputFreq, Consumer<IntervalMeasurement> intervalReporter) {
        this.sourceName = sourceName;
        this.outputFreq = outputFreq;
        this.intervalReporter = intervalReporter;
        this.sentTimestamps = new ConcurrentHashMap<>();
        this.receivedTimestamps = new LongSparseArray<>();
        this.count = 0;
        this.startTime = 0;
        this.intervalStartTime = 0;
        this.lastReceiveTime = 0;
        this.overallTotalRtt = 0;
    }

    public void logSend(long frameId, long time) {
        this.sentTimestamps.put(frameId, time);
        if (this.startTime == 0) {
            this.startTime = time;
            this.intervalStartTime = time;
        }
    }

    public void logReceive(long frameId, long time) {
        this.receivedTimestamps.put(frameId, time);
        this.count++;  // Count should always be frameId + 1
        this.lastReceiveTime = time;

        Long sentTime = this.sentTimestamps.get(frameId);
        if (sentTime == null) {
            Log.e(TAG, "No Sent time for frameID: " + frameId);
        } else {
            this.overallTotalRtt += (time - sentTime);
        }

        if ((this.count % this.outputFreq) == 0) {
            double intervalRtt = this.computeIntervalRtt();
            double overallRtt = this.computeOverallRtt();
            double intervalFps = this.computeIntervalFps();
            double overallFps = this.computeOverallFps();
            this.intervalReporter.accept(new IntervalMeasurement(
                    this.sourceName, intervalRtt, overallRtt, intervalFps, overallFps));
            this.intervalStartTime = SystemClock.elapsedRealtime();
        }
    }

    private double computeIntervalRtt() {
        long totalRtt = 0;
        for (int i = 0; i < this.receivedTimestamps.size(); i++) {
            long frameId = this.receivedTimestamps.keyAt(i);
            Long receivedTimestamp = this.receivedTimestamps.valueAt(i);

            Long sentTimestamp = this.sentTimestamps.remove(frameId);
            assert sentTimestamp != null;
            totalRtt += (receivedTimestamp - sentTimestamp);
        }
        int numFrames = this.receivedTimestamps.size();

        // Do not clear this.sentTimestamps because we might have sent frames and not received
        // responses yet. Note that timestamps we don't need are removed when
        // this.sentTimestamps#remove is called above
        this.receivedTimestamps.clear();

        return ((double)totalRtt) / numFrames;
    }

    public double computeOverallRtt() {
        return (double)this.overallTotalRtt / this.count;
    }

    public double computeIntervalFps() {
        return this.computeFps(this.outputFreq, this.intervalStartTime);
    }

    public double computeOverallFps() {
        return this.computeFps(this.count, this.startTime);
    }

    private double computeFps(long numFrames, long startTime) {
        return (double)numFrames / ((this.lastReceiveTime - startTime) / 1000.0);
    }
}
