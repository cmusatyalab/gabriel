package edu.cmu.cs.gabrielclient.network;

import android.os.Handler;

import edu.cmu.cs.gabrielclient.control.TokenController;

public class ConnectionConfig {
    public String serverIP;
    public int serverPort;
    public TokenController tc;
    public Handler callerHandler;
    public LogicalTime lt;

    public ConnectionConfig(String serverIP, int serverPort, TokenController tc, Handler
            callerHandler, LogicalTime lt) {
        this.serverIP = serverIP;
        this.serverPort = serverPort;
        this.tc = tc;
        this.callerHandler = callerHandler;
        this.lt = lt;
    }
}
