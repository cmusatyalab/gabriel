package edu.cmu.cs.gabriel.client.observer;

import android.os.SystemClock;

import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Consumer;

import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;
import edu.cmu.cs.gabriel.protocol.Protos.ToClient;

public class MeasurementResultObserver extends ResultObserver {
    private final int outputFreq;
    private final Consumer<SourceRttFps> intervalReporter;
    private final Map<String, MeasurementSource> measurementSources;

    public MeasurementResultObserver(
            int tokenLimit, Consumer<ResultWrapper> resultConsumer,
            int outputFreq, Consumer<SourceRttFps> intervalReporter) {
        super(tokenLimit, resultConsumer);
        this.outputFreq = outputFreq;
        this.intervalReporter = intervalReporter;
        this.measurementSources = new HashMap<>();
    }

    @Override
    void processWelcome(ToClient.Welcome welcome) {
        super.processWelcome(welcome);
        for (String sourceName : welcome.getSourcesConsumedList()) {
            MeasurementSource measurementSource = new MeasurementSource(
                    sourceName, this.outputFreq, this.intervalReporter);
            this.measurementSources.put(sourceName, measurementSource);
        }
    }

    @Override
    void processResponse(ToClient.Response response) {
        long time = SystemClock.elapsedRealtime();
        super.processResponse(response);
        String sourceName = response.getSourceName();
        Objects.requireNonNull(this.measurementSources.get(sourceName)).logReceive(
                response.getFrameId(), time);
    }

    public void logSend(String sourceName, long frameId, long time) {
        Objects.requireNonNull(this.measurementSources.get(sourceName)).logSend(frameId, time);
    }

    public double computeOverallFps(String sourceName) {
        return Objects.requireNonNull(this.measurementSources.get(sourceName)).computeOverallFps();
    }
}
