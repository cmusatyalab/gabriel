package edu.cmu.cs.gabriel.client.comm;

import android.app.Application;

import java.util.function.Consumer;
import java.util.function.Supplier;

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

    ServerComm(String endpoint, int port, Application application, Consumer<ErrorType> onDisconnect,
               ResultObserver resultObserver) {
        this.socketWrapper = new SocketWrapper(
                endpoint, port, application, onDisconnect, resultObserver);
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
    public boolean sendBlocking(InputFrame inputFrame, String sourceName, boolean wait) {
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
