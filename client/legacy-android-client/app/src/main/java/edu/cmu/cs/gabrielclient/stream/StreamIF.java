package edu.cmu.cs.gabrielclient.stream;

import android.os.Handler;

import edu.cmu.cs.gabrielclient.network.LogicalTime;
import edu.cmu.cs.gabrielclient.token.TokenController;

public interface StreamIF {
    void init(StreamConfig config);

    void start();

    void stop();

    class StreamConfig {
        public String serverIP;
        public int serverPort;
        public TokenController tc;
        public Handler callerHandler;
        public LogicalTime lt;

        public StreamConfig(String serverIP, int serverPort, TokenController tc, Handler callerHandler, LogicalTime
                lt) {
            this.serverIP = serverIP;
            this.serverPort = serverPort;
            this.tc = tc;
            this.callerHandler = callerHandler;
            this.lt = lt;
        }
    }
}
