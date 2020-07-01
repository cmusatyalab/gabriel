package edu.cmu.cs.gabriel.client.observer;

import android.util.Log;

import com.google.protobuf.InvalidProtocolBufferException;
import com.tinder.scarlet.Stream.Observer;

import org.jetbrains.annotations.NotNull;

import java.util.HashMap;
import java.util.Map;
import java.util.Objects;
import java.util.function.Consumer;

import edu.cmu.cs.gabriel.client.comm.Source;
import edu.cmu.cs.gabriel.protocol.Protos.ToClient;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class ResultObserver implements Observer<byte[]>  {
    private static final String TAG = "ResultObserver";

    private final Map<String, Source> sources;
    private final int tokenLimit;
    private final Consumer<ResultWrapper> resultConsumer;
    private Runnable onErrorResult;
    private boolean receivedWelcome;


    public ResultObserver(int tokenLimit, Consumer<ResultWrapper> resultConsumer) {
        this.sources = new HashMap<>();
        this.tokenLimit = tokenLimit;
        this.resultConsumer = resultConsumer;
        this.onErrorResult = null;
        this.receivedWelcome = false;
    }

    private void ensureReceivedWelcome() {
        if (this.receivedWelcome) {
            return;
        }

        synchronized (this) {
            while (!this.receivedWelcome) {
                try {
                    this.wait();
                } catch (InterruptedException e) {
                    Log.w(TAG, e);
                }
            }
        }
    }

    public boolean sourceFor(String sourceName) {
        this.ensureReceivedWelcome();
        return this.sources.containsKey(sourceName);
    }

    public Source getSource(String sourceName) {
        this.ensureReceivedWelcome();
        return this.sources.get(sourceName);
    }

    public void setOnErrorResult(Runnable onErrorResult){
        this.onErrorResult = onErrorResult;
    }

    @Override
    public void onNext(byte[] rawToClient) {
        try {
            ToClient toClient = ToClient.parseFrom(rawToClient);
            switch (toClient.getWelcomeOrResponseCase()) {
                case WELCOME:
                    this.processWelcome(toClient.getWelcome());
                    return;
                case RESPONSE:
                    this.processResponse(toClient.getResponse());
                    return;
                case WELCOMEORRESPONSE_NOT_SET:
                    throw new RuntimeException("Server sent empty message");
                default:
                    String value = toClient.getWelcomeOrResponseCase().name();
                    throw new IllegalStateException("Unexpected toClient value: " + value);
            }
        } catch (InvalidProtocolBufferException e) {
            Log.e(TAG, "Failed to parse ToClient", e);
        }
    }

    synchronized void processWelcome(ToClient.Welcome welcome) {
        int numTokens = Math.min(welcome.getNumTokensPerSource(), this.tokenLimit);
        for (String sourceName : welcome.getSourcesConsumedList()) {
            this.sources.put(sourceName, new Source(numTokens));
        }
        this.receivedWelcome = true;
        this.notify();
    }

    void processResponse(ToClient.Response response) {
        if (response.getReturnToken()) {
            Objects.requireNonNull(this.sources.get(response.getSourceName())).returnToken();
        }

        ResultWrapper resultWrapper = response.getResultWrapper();
        ResultWrapper.Status status = resultWrapper.getStatus();
        if ((this.onErrorResult != null) && (status == ResultWrapper.Status.ENGINE_ERROR ||
                status == ResultWrapper.Status.WRONG_INPUT_FORMAT ||
                status == ResultWrapper.Status.NO_ENGINE_FOR_SOURCE)) {
            this.onErrorResult.run();
        }

        if (status != ResultWrapper.Status.SUCCESS) {
            Log.e(TAG, "Output status was: " + status.name());
            return;
        }

        this.resultConsumer.accept(resultWrapper);
    }

    @Override
    public void onError(@NotNull Throwable throwable) {
        Log.e(TAG, "onError", throwable);
    }

    @Override
    public void onComplete() {
        Log.i(TAG, "onComplete");
    }
}
