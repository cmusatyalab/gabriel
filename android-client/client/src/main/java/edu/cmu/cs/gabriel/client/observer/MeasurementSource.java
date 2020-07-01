package edu.cmu.cs.gabriel.client.observer;

import android.os.SystemClock;
import android.util.LongSparseArray;

import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Consumer;

public class MeasurementSource {
    private final String sourceName;
    private final int outputFreq;
    private final Consumer<SourceRttFps> intervalReporter;
    private final Map<Long, Long> sentTimestamps;
    private final LongSparseArray<Long> receivedTimestamps;
    private long count;
    private long startTime;
    private long intervalStartTime;
    private long lastReceiveTime;

    public MeasurementSource(
            String sourceName, int outputFreq, Consumer<SourceRttFps> intervalReporter) {
        this.sourceName = sourceName;
        this.outputFreq = outputFreq;
        this.intervalReporter = intervalReporter;
        this.sentTimestamps = new ConcurrentHashMap<>();
        this.receivedTimestamps = new LongSparseArray<>();
        this.count = 0;
        this.startTime = 0;
        this.intervalStartTime = 0;
        this.lastReceiveTime = 0;
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
        this.count++; // Count should always be frameId + 1

        if ((this.count % this.outputFreq) == 0) {
            double rtt = this.computeIntervalRtt();
            double fps = this.computeIntervalFps(time);
            this.intervalReporter.accept(new SourceRttFps(this.sourceName, rtt, fps));
            this.intervalStartTime = SystemClock.elapsedRealtime();
        }
        this.lastReceiveTime = time;
    }

    public double computeIntervalFps(long currentTime) {
        return MeasurementSource.computeFps(this.outputFreq, currentTime, this.intervalStartTime);
    }

    public double computeOverallFps() {
        return MeasurementSource.computeFps(this.count, this.lastReceiveTime, this.startTime);
    }

    private static double computeFps(long numFrames, long currentTime, long startTime) {
        return (double)numFrames / ((currentTime - startTime) / 1000.0);
    }

    public double computeIntervalRtt() {
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
        // his.sentTimestamps#remove is called above
        this.receivedTimestamps.clear();
        return ((double)totalRtt) / numFrames;
    }
}
