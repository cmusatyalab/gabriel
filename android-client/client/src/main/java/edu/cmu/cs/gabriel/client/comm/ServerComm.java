package edu.cmu.cs.gabriel.client.comm;

import static androidx.core.content.ContextCompat.getSystemService;

import android.app.Application;
import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;

import java.util.function.Consumer;
import java.util.function.Supplier;

import edu.cmu.cs.gabriel.client.observer.MeasurementResultObserver;
import edu.cmu.cs.gabriel.client.observer.ResultObserver;
import edu.cmu.cs.gabriel.client.results.ErrorType;
import edu.cmu.cs.gabriel.client.results.SendSupplierResult;
import edu.cmu.cs.gabriel.client.socket.SocketWrapper;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;
import edu.cmu.cs.gabriel.protocol.Protos.InputFrame;
import edu.cmu.cs.gabriel.protocol.Protos.FromClient;

public class ServerComm {
    private final SocketWrapper socketWrapper;
    private final ResultObserver resultObserver;

    public ServerComm(String endpoint, int port, Application application, Consumer<ErrorType> onDisconnect, ResultObserver resultObserver) {
        ConnectivityManager cm = (ConnectivityManager) application.getApplicationContext().getSystemService(Context.CONNECTIVITY_SERVICE);
        this.socketWrapper = new SocketWrapper(
                endpoint, port, application, onDisconnect, resultObserver, cm.getActiveNetwork());
        this.resultObserver = resultObserver;
    }

    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect, int tokenLimit) {
        ResultObserver resultObserver = new ResultObserver(tokenLimit, resultConsumer);
        return new ServerComm(endpoint, port, application, onDisconnect, resultObserver);
    }

    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect) {
        return ServerComm.createServerComm(
                resultConsumer, endpoint, port, application, onDisconnect, Integer.MAX_VALUE);
    }

    public static ServerComm createServerComm(
            Consumer<ResultWrapper> resultConsumer, String endpoint, int port,
            Application application, Consumer<ErrorType> onDisconnect, Network network) {
        ResultObserver resultObserver = new ResultObserver(Integer.MAX_VALUE, resultConsumer);
        return new ServerComm(endpoint, port, application, onDisconnect, resultObserver, network);
    }

    ServerComm(String endpoint, int port, Application application, Consumer<ErrorType> onDisconnect,
               ResultObserver resultObserver, Network network) {
        this.socketWrapper = new SocketWrapper(
                endpoint, port, application, onDisconnect, resultObserver, network);
        this.resultObserver = resultObserver;
    }

    void sendFromClient(FromClient fromClient) {
        this.socketWrapper.send(fromClient);
    }

    private void sendHelper(Source source, String sourceName, InputFrame inputFrame) {
        long frameId = source.nextFrame();
        FromClient fromClient = FromClient.newBuilder()
                .setFrameId(frameId)
                .setSourceName(sourceName)
                .setInputFrame(inputFrame).build();
        this.sendFromClient(fromClient);
    }

    /**
     * Check if the server has a cognitive engine that consumes input for sourceName
     *
     * @return true if server accepts input for sourceName, false if not.
     */
    public boolean acceptsInputForSource(String sourceName) {
        return this.resultObserver.sourceFor(sourceName);
    }

    /**
     * Attempt to get token. Then send frame if we got token successfully.
     *
     * @param inputFrame item to send to server
     * @param wait If true, will block until a token is available.
     * @return True if send succeeded.
     */
    public boolean send(InputFrame inputFrame, String sourceName, boolean wait) {
        Source source = this.resultObserver.getSource(sourceName);
        boolean gotToken = source.getToken(wait);
        if (!gotToken) {
            return false;
        }

        this.sendHelper(source, sourceName, inputFrame);
        return true;
    }

    /**
     * Attempt to get token. Then call @param supplier to get the frame to send.
     * */
    public SendSupplierResult sendSupplier(
            Supplier<InputFrame> supplier, String sourceName, boolean wait) {
        Source source = this.resultObserver.getSource(sourceName);
        boolean gotToken = source.getToken(wait);
        if (!gotToken) {
            return SendSupplierResult.ERROR_GETTING_TOKEN;
        }

        InputFrame inputFrame = supplier.get();
        if (inputFrame == null) {
            source.returnToken();
            return SendSupplierResult.NULL_FROM_SUPPLIER;
        }

        this.sendHelper(source, sourceName, inputFrame);
        return SendSupplierResult.SUCCESS;
    }

    public void stop() {
        this.socketWrapper.stop();
    }

    public boolean isRunning() {
        return this.socketWrapper.isRunning();
    }
}
