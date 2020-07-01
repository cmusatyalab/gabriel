package edu.cmu.cs.gabriel.client.comm;

import android.app.Application;

import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.observer.MeasurementResultObserver;
import edu.cmu.cs.gabriel.client.results.ErrorType;
import edu.cmu.cs.gabriel.client.observer.SourceRttFps;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class MeasurementServerComm {
    private static final int DEFAULT_OUTPUT_FREQ = 10;

    // TODO: Replace these factory methods with a builder to allow setting tokenLimit without
    //       setting outputFreq
    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect,
            Consumer<SourceRttFps> intervalReporter, int tokenLimit, int outputFreq) {
        MeasurementResultObserver measurementResultObserver = new MeasurementResultObserver(
                tokenLimit, resultConsumer, outputFreq, intervalReporter);

        return new ServerComm(endpoint, port, application, onDisconnect, measurementResultObserver);
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
}
