package edu.cmu.cs.gabrielclient.control;

import edu.cmu.cs.gabrielclient.network.ConnectionConfig;
import edu.cmu.cs.gabrielclient.network.TwoWayMessageThread;
import edu.cmu.cs.gabrielclient.util.LifeCycleIF;

public class ServerController implements LifeCycleIF {

    private static final String LOG_TAG = ServerController.class.getSimpleName();

    private TwoWayMessageThread serverControlThread;
    private ConnectionConfig config;

    public ServerController(ConnectionConfig config) {
        this.config = config;
    }

    @Override
    public void onResume() {
        serverControlThread = new TwoWayMessageThread(config);
    }

    @Override
    public void onPause() {
        if (serverControlThread!=null && serverControlThread.isAlive()){
            serverControlThread.close();
            serverControlThread = null;
        }
    }

    @Override
    public void onDestroy() {

    }
}
