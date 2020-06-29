package edu.cmu.cs.gabriel.client.comm;

import android.app.Application;
import android.os.SystemClock;
import android.util.Log;
import android.util.LongSparseArray;

import edu.cmu.cs.gabriel.client.observer.ResultObserver;
import edu.cmu.cs.gabriel.client.socket.MeasurementSocketWrapper;
import edu.cmu.cs.gabriel.client.function.Consumer;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class MeasurementServerComm extends ServerCommCore {
    private static final String TAG = "MeasurementServerComm";
    private final static int DEFAULT_OUTPUT_FREQ = 10;

    private LongSparseArray<Long> receivedTimestamps;
    private MeasurementSocketWrapper measurementSocketWrapper;
    private long startTime;
    private long intervalStartTime;

    // TODO: Replace these constructors with a builder to allow setting tokenLimit without setting
    //       outputFreq
    public MeasurementServerComm(
            final Consumer<ResultWrapper> consumer, Consumer<ErrorType> onDisconnect,
            String serverURL, Application application, final Consumer<RttFps> intervalReporter,
            int tokenLimit, final int outputFreq) {
        super(onDisconnect, tokenLimit);

        this.receivedTimestamps = new LongSparseArray<>();

        Consumer<ResultWrapper> timingConsumer = new Consumer<ResultWrapper>() {
            @Override
            public void accept(ResultWrapper resultWrapper) {
                long currentTime = SystemClock.elapsedRealtime();
                consumer.accept(resultWrapper);
                long frameId = resultWrapper.getFrameId();
                MeasurementServerComm.this.receivedTimestamps.put(frameId, currentTime);
                int numReceived = MeasurementServerComm.this.receivedTimestamps.size();

                if (outputFreq > 0 && (numReceived % outputFreq == 0)) {
                    double intervalRtt = MeasurementServerComm.this.computeRtt(
                            numReceived - outputFreq);
                    double intervalFps = MeasurementServerComm.this.computeFps(
                            outputFreq, currentTime, MeasurementServerComm.this.intervalStartTime);
                    intervalReporter.accept(new RttFps(intervalRtt, intervalFps));

                    MeasurementServerComm.this.intervalStartTime = SystemClock.elapsedRealtime();
                }
            }
        };

        ResultObserver resultObserver = new ResultObserver(
                this.tokenManager, timingConsumer, this.onErrorResult);
        this.measurementSocketWrapper = new MeasurementSocketWrapper(
                serverURL, application, this.lifecycleRegistry, resultObserver, this.eventObserver);
        this.socketWrapper = this.measurementSocketWrapper;

        this.setTimes();
    }

    public MeasurementServerComm(
            Consumer<ResultWrapper> consumer, Consumer<ErrorType> onDisconnect, String serverURL,
            Application application, Consumer<RttFps> intervalReporter, int tokenLimit) {
        this(consumer, onDisconnect, serverURL, application, intervalReporter, tokenLimit,
                MeasurementServerComm.DEFAULT_OUTPUT_FREQ);
    }

    public MeasurementServerComm(
            Consumer<ResultWrapper> consumer, Consumer<ErrorType> onDisconnect, String serverURL,
            Application application, Consumer<RttFps> intervalReporter) {
        this(consumer, onDisconnect, serverURL, application, intervalReporter, Integer.MAX_VALUE);
    }

    private void setTimes() {
        this.startTime = SystemClock.elapsedRealtime();
        this.intervalStartTime = this.startTime;
    }

    private double computeFps(int numFrames, long currentTime, long startTime) {
        return (double)numFrames / ((currentTime - startTime) / 1000.0);
    }

    private double computeRtt(int startingFrame) {
        long totalRtt = 0;
        int numFrames = this.receivedTimestamps.size() - 1 - startingFrame;

        for (int i = startingFrame; i < this.receivedTimestamps.size(); i++) {
            long frameId = this.receivedTimestamps.keyAt(i);
            Long receivedTimestamp = this.receivedTimestamps.valueAt(i);

            Long sentTimestamp = this.measurementSocketWrapper.getSentTimestamps().get(frameId);
            totalRtt += (receivedTimestamp - sentTimestamp);
        }
        return ((double)totalRtt) / numFrames;
    }

    public double getOverallAvgRtt() {
        return this.computeRtt(0);
    }

    public double getOverallFps() {
        return computeFps(
                MeasurementServerComm.this.receivedTimestamps.size(), SystemClock.elapsedRealtime(),
                MeasurementServerComm.this.startTime);
    }

    public void clearMeasurements() {
        this.measurementSocketWrapper.clearSentTimestamps();
        this.receivedTimestamps.clear();
        this.setTimes();
    }
}
