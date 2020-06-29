package edu.cmu.cs.gabriel.client.comm;

import android.app.Application;

import edu.cmu.cs.gabriel.client.observer.ResultObserver;
import edu.cmu.cs.gabriel.client.socket.SocketWrapper;
import edu.cmu.cs.gabriel.client.function.Consumer;
import edu.cmu.cs.gabriel.protocol.Protos.ResultWrapper;

public class ServerComm extends ServerCommCore {
    public ServerComm(Consumer<ResultWrapper> resultConsumer, Consumer<ErrorType> onDisconnect,
                      String serverURL, Application application, int tokenLimit) {
        super(onDisconnect, tokenLimit);

        ResultObserver resultObserver = new ResultObserver(
                this.tokenManager, resultConsumer, this.onErrorResult);

        this.socketWrapper = new SocketWrapper(
                serverURL, application, this.lifecycleRegistry, resultObserver, this.eventObserver);
    }

    public ServerComm(Consumer<ResultWrapper> resultConsumer, Consumer<ErrorType> onDisconnect,
                      String serverURL, Application application) {
        this(resultConsumer, onDisconnect, serverURL, application, Integer.MAX_VALUE);
    }
}
