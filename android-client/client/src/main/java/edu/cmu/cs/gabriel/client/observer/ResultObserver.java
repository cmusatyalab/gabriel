package edu.cmu.cs.gabriel.client.observer;

import android.annotation.SuppressLint;
import android.util.Log;

import com.google.protobuf.InvalidProtocolBufferException;
import com.tinder.scarlet.Stream.Observer;

import org.jetbrains.annotations.NotNull;

import edu.cmu.cs.gabriel.client.function.Consumer;
import edu.cmu.cs.gabriel.client.token.TokenManager;
import edu.cmu.cs.gabriel.protocol.Protos.ToClient;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class ResultObserver implements Observer<byte[]>  {
    private static final String TAG = "ResultObserver";

    private TokenManager tokenManager;
    private Consumer<ResultWrapper> consumer;
    private Runnable onErrorResult;


    public ResultObserver(
            TokenManager tokenManager, Consumer<ResultWrapper> consumer, Runnable onErrorResult) {
        this.tokenManager = tokenManager;
        this.consumer = consumer;
        this.onErrorResult = onErrorResult;
    }

    @SuppressLint("Assert")
    @Override
    public void onNext(byte[] rawToClient) {
        ToClient toClient;
        try {
            toClient = ToClient.parseFrom(rawToClient);
        } catch (InvalidProtocolBufferException e) {
            Log.e(TAG, "Failed to parse ToClient", e);
            return;
        }

        switch (toClient.getWelcomeOrResultCase()) {
            case WELCOME_MESSAGE:
                this.tokenManager.setTokensFromWelcomeMessage(toClient.getWelcomeMessage());

                assert !toClient.getReturnToken();
                return;
            case RESULT_WRAPPER:
                ResultWrapper resultWrapper = toClient.getResultWrapper();
                if (toClient.getReturnToken()) {
                    this.tokenManager.returnToken(resultWrapper.getFilterPassed());
                }

                ResultWrapper.Status status = resultWrapper.getStatus();
                if (status == ResultWrapper.Status.ENGINE_ERROR ||
                        status == ResultWrapper.Status.WRONG_INPUT_FORMAT ||
                        status == ResultWrapper.Status.NO_ENGINE_FOR_FILTER_PASSED) {
                    this.onErrorResult.run();
                }
                
                if (status != ResultWrapper.Status.SUCCESS) {
                    Log.e(TAG, "Output status was: " + status.name());
                    return;
                }

                consumer.accept(resultWrapper);
                return;
            case WELCOMEORRESULT_NOT_SET:
                throw new RuntimeException("Server sent empty message");
            default:
                throw new IllegalStateException("Unexpected toClient value: " +
                        toClient.getWelcomeOrResultCase().name());
        }
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
