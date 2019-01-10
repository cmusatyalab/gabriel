package edu.cmu.cs.gabrielclient.sensorstream;

import android.os.Handler;

import edu.cmu.cs.gabrielclient.network.LogicalTime;
import edu.cmu.cs.gabrielclient.token.TokenController;

public interface SensorStreamIF {
    void init(SensorStreamConfig config);

    void start();

    void stop();

    class SensorStreamConfig {
        public String serverIP;
        public int serverPort;
        public TokenController tc;
        public Handler returnMsgHandler;
        public LogicalTime lt;
    }
}
