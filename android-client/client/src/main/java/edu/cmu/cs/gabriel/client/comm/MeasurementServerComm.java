package edu.cmu.cs.gabriel.client.comm;

import android.app.Application;
import android.os.SystemClock;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.MeasurementResultObserver;
import edu.cmu.cs.gabriel.client.results.ErrorType;
import edu.cmu.cs.gabriel.client.observer.SourceRttFps;
import edu.cmu.cs.gabriel.protocol.Protos.FromClient;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class MeasurementServerComm extends ServerComm {
    private static final int DEFAULT_OUTPUT_FREQ = 10;

    private final MeasurementResultObserver measurementResultObserver;

    // TODO: Replace these factory methods with a builder to allow setting tokenLimit without
    //       setting outputFreq
    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect,
            Consumer<SourceRttFps> intervalReporter, int tokenLimit, int outputFreq) {
        MeasurementResultObserver measurementResultObserver = new MeasurementResultObserver(
                tokenLimit, resultConsumer, outputFreq, intervalReporter);

        return new MeasurementServerComm(
                endpoint, port, application, onDisconnect, measurementResultObserver);
    }

    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect,
            Consumer<SourceRttFps> intervalReporter, int tokenLimit) {
        return MeasurementServerComm.createServerComm(
                resultConsumer, endpoint, port, application, onDisconnect, intervalReporter,
                tokenLimit, DEFAULT_OUTPUT_FREQ);
    }

    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect,
            Consumer<SourceRttFps> intervalReporter) {
        return MeasurementServerComm.createServerComm(
                resultConsumer, endpoint, port, application, onDisconnect, intervalReporter,
                Integer.MAX_VALUE);
    }

    MeasurementServerComm(
            String endpoint, int port, Application application, Consumer<ErrorType> onDisconnect,
            MeasurementResultObserver measurementResultObserver) {
        super(endpoint, port, application, onDisconnect, measurementResultObserver);
        this.measurementResultObserver = measurementResultObserver;
    }

    @Override
    void sendFromClient(FromClient fromClient) {
        super.sendFromClient(fromClient);
        long time = SystemClock.elapsedRealtime();
        this.measurementResultObserver.logSend(
                fromClient.getSourceName(), fromClient.getFrameId(), time);
    }

    public double computeOverallFps(String sourceName) {
        return this.measurementResultObserver.computeOverallFps(sourceName);
    }
}
